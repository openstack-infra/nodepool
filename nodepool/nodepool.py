#!/usr/bin/env python

# Copyright (C) 2011-2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import apscheduler.schedulers.background
import apscheduler.triggers.cron
import gear
import json
import logging
import os.path
import paramiko
import pprint
import random
import re
import threading
import time
from uuid import uuid4
import zmq

import allocation
import jenkins_manager
import nodedb
import exceptions
import nodeutils as utils
import provider_manager
import stats
import config as nodepool_config

import jobs

MINS = 60
HOURS = 60 * MINS

WATERMARK_SLEEP = 10         # Interval between checking if new servers needed
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
CONNECT_TIMEOUT = 10 * MINS  # How long to try to connect after a server
                             # is ACTIVE
NODE_CLEANUP = 8 * HOURS     # When to start deleting a node that is not
                             # READY or HOLD
TEST_CLEANUP = 5 * MINS      # When to start deleting a node that is in TEST
IMAGE_CLEANUP = 8 * HOURS    # When to start deleting an image that is not
                             # READY or is not the current or previous image
DELETE_DELAY = 1 * MINS      # Delay before deleting a node that has completed
                             # its job.


class LaunchNodepoolException(Exception):
    statsd_key = 'error.nodepool'


class LaunchStatusException(Exception):
    statsd_key = 'error.status'


class LaunchNetworkException(Exception):
    statsd_key = 'error.network'


class LaunchAuthException(Exception):
    statsd_key = 'error.auth'


class NodeCompleteThread(threading.Thread):
    log = logging.getLogger("nodepool.NodeCompleteThread")

    def __init__(self, nodepool, nodename, jobname, result, branch):
        threading.Thread.__init__(self, name='NodeCompleteThread for %s' %
                                  nodename)
        self.nodename = nodename
        self.nodepool = nodepool
        self.jobname = jobname
        self.result = result
        self.branch = branch
        self.statsd = stats.get_client()

    def run(self):
        try:
            with self.nodepool.getDB().getSession() as session:
                self.handleEvent(session)
        except Exception:
            self.log.exception("Exception handling event for %s:" %
                               self.nodename)

    def handleEvent(self, session):
        node = session.getNodeByNodename(self.nodename)
        if not node:
            self.log.debug("Unable to find node with nodename: %s" %
                           self.nodename)
            return

        if node.state == nodedb.HOLD:
            self.log.info("Node id: %s is complete but in HOLD state" %
                          node.id)
            return

        nodepool_job = session.getJobByName(self.jobname)
        if (nodepool_job and nodepool_job.hold_on_failure and
            self.result != 'SUCCESS'):
            held_nodes = session.getNodes(state=nodedb.HOLD)
            held_nodes = [n for n in held_nodes if self.jobname in n.comment]
            if len(held_nodes) >= nodepool_job.hold_on_failure:
                self.log.info("Node id: %s has failed %s but %s nodes "
                              "are already held for that job" % (
                                  node.id, self.jobname, len(held_nodes)))
            else:
                node.state = nodedb.HOLD
                node.comment = "Automatically held after failing %s" % (
                    self.jobname,)
                self.log.info("Node id: %s failed %s, automatically holding" % (
                    node.id, self.jobname))
                self.nodepool.updateStats(session, node.provider_name)
                return

        target = self.nodepool.config.targets[node.target_name]
        if self.jobname == target.jenkins_test_job:
            self.log.debug("Test job for node id: %s complete, result: %s" %
                           (node.id, self.result))
            if self.result == 'SUCCESS':
                jenkins = self.nodepool.getJenkinsManager(target)
                old = jenkins.relabelNode(node.nodename, [node.image_name])
                if not old:
                    old = '[unlabeled]'
                self.log.info("Relabeled jenkins node id: %s from %s to %s" %
                              (node.id, old, node.image_name))
                node.state = nodedb.READY
                self.log.info("Node id: %s is ready" % node.id)
                self.nodepool.updateStats(session, node.provider_name)
                return
            self.log.info("Node id: %s failed acceptance test, deleting" %
                          node.id)

        if self.statsd and self.result == 'SUCCESS':
            start = node.state_time
            dt = int((time.time() - start) * 1000)

            # nodepool.job.tempest
            key = 'nodepool.job.%s' % self.jobname
            self.statsd.timing(key + '.runtime', dt)
            self.statsd.incr(key + '.builds')

            # nodepool.job.tempest.master
            key += '.%s' % self.branch
            self.statsd.timing(key + '.runtime', dt)
            self.statsd.incr(key + '.builds')

            # nodepool.job.tempest.master.devstack-precise
            key += '.%s' % node.label_name
            self.statsd.timing(key + '.runtime', dt)
            self.statsd.incr(key + '.builds')

            # nodepool.job.tempest.master.devstack-precise.rax-ord
            key += '.%s' % node.provider_name
            self.statsd.timing(key + '.runtime', dt)
            self.statsd.incr(key + '.builds')

        time.sleep(DELETE_DELAY)
        self.nodepool.deleteNode(node.id)


class NodeUpdateListener(threading.Thread):
    log = logging.getLogger("nodepool.NodeUpdateListener")

    def __init__(self, nodepool, addr):
        threading.Thread.__init__(self, name='NodeUpdateListener')
        self.nodepool = nodepool
        self.socket = self.nodepool.zmq_context.socket(zmq.SUB)
        self.socket.RCVTIMEO = 1000
        event_filter = b""
        self.socket.setsockopt(zmq.SUBSCRIBE, event_filter)
        self.socket.connect(addr)
        self._stopped = False

    def run(self):
        while not self._stopped:
            try:
                m = self.socket.recv().decode('utf-8')
            except zmq.error.Again:
                continue
            try:
                topic, data = m.split(None, 1)
                self.handleEvent(topic, data)
            except Exception:
                self.log.exception("Exception handling job:")

    def stop(self):
        self._stopped = True

    def handleEvent(self, topic, data):
        self.log.debug("Received: %s %s" % (topic, data))
        args = json.loads(data)
        build = args['build']
        if 'node_name' not in build:
            return
        jobname = args['name']
        nodename = args['build']['node_name']
        if topic == 'onStarted':
            self.handleStartPhase(nodename, jobname)
        elif topic == 'onCompleted':
            pass
        elif topic == 'onFinalized':
            result = args['build'].get('status')
            params = args['build'].get('parameters')
            if params:
                branch = params.get('ZUUL_BRANCH', 'unknown_branch')
            else:
                branch = 'unknown_branch'
            self.handleCompletePhase(nodename, jobname, result, branch)
        else:
            raise Exception("Received job for unhandled phase: %s" %
                            topic)

    def handleStartPhase(self, nodename, jobname):
        with self.nodepool.getDB().getSession() as session:
            node = session.getNodeByNodename(nodename)
            if not node:
                self.log.debug("Unable to find node with nodename: %s" %
                               nodename)
                return

            target = self.nodepool.config.targets[node.target_name]
            if jobname == target.jenkins_test_job:
                self.log.debug("Test job for node id: %s started" % node.id)
                return

            # Preserve the HOLD state even if a job starts on the node.
            if node.state != nodedb.HOLD:
                self.log.info("Setting node id: %s to USED" % node.id)
                node.state = nodedb.USED
            self.nodepool.updateStats(session, node.provider_name)

    def handleCompletePhase(self, nodename, jobname, result, branch):
        t = NodeCompleteThread(self.nodepool, nodename, jobname, result,
                               branch)
        t.start()


class JobTracker(object):
    def __init__(self):
        self._running_jobs = set()

    @property
    def running_jobs(self):
        return list(self._running_jobs)

    def addJob(self, job):
        self._running_jobs.add(job)
        job.addCompletionHandler(self._running_jobs.remove)


class GearmanClient(gear.Client):
    def __init__(self):
        super(GearmanClient, self).__init__(client_id='nodepool')
        self.__log = logging.getLogger("nodepool.GearmanClient")

    def getNeededWorkers(self):
        needed_workers = {}
        job_worker_map = {}
        unspecified_jobs = {}
        for connection in self.active_connections:
            try:
                req = gear.StatusAdminRequest()
                connection.sendAdminRequest(req, timeout=300)
            except Exception:
                self.__log.exception("Exception while listing functions")
                self._lostConnection(connection)
                continue
            for line in req.response.split('\n'):
                parts = [x.strip() for x in line.split('\t')]
                # parts[0] - function name
                # parts[1] - total jobs queued (including building)
                # parts[2] - jobs building
                # parts[3] - workers registered
                if not parts or parts[0] == '.':
                    continue
                if not parts[0].startswith('build:'):
                    continue
                function = parts[0][len('build:'):]
                # total jobs in queue (including building jobs)
                # NOTE(jhesketh): Jobs that are being built are accounted for
                # in the demand algorithm by subtracting the running nodes.
                # If there are foreign (to nodepool) workers accepting jobs
                # the demand will be higher than actually required. However
                # better to have too many than too few and if you have a
                # foreign worker this may be desired.
                try:
                    queued = int(parts[1])
                except ValueError as e:
                    self.__log.warn(
                        'Server returned non-integer value in status. (%s)' %
                        str(e))
                    queued = 0
                if queued > 0:
                    self.__log.debug("Function: %s queued: %s" % (function,
                                                                  queued))
                if ':' in function:
                    fparts = function.split(':')
                    # fparts[0] - function name
                    # fparts[1] - target node [type]
                    job = fparts[-2]
                    worker = fparts[-1]
                    workers = job_worker_map.get(job, [])
                    workers.append(worker)
                    job_worker_map[job] = workers
                    if queued > 0:
                        needed_workers[worker] = (
                            needed_workers.get(worker, 0) + queued)
                elif queued > 0:
                    job = function
                    unspecified_jobs[job] = (unspecified_jobs.get(job, 0) +
                                             queued)
        for job, queued in unspecified_jobs.items():
            workers = job_worker_map.get(job)
            if not workers:
                continue
            worker = workers[0]
            needed_workers[worker] = (needed_workers.get(worker, 0) +
                                      queued)
        return needed_workers

    def handleWorkComplete(self, packet):
        job = super(GearmanClient, self).handleWorkComplete(packet)
        job.onCompleted()

    def handleWorkFail(self, packet):
        job = super(GearmanClient, self).handleWorkFail(packet)
        job.onFailed()

    def handleWorkException(self, packet):
        job = super(GearmanClient, self).handleWorkException(packet)
        job.onFailed()

    def handleDisconnect(self, job):
        super(GearmanClient, self).handleDisconnect(job)
        job.onDisconnect()

    def handleWorkStatus(self, packet):
        job = super(GearmanClient, self).handleWorkStatus(packet)
        job.onWorkStatus()


class InstanceDeleter(threading.Thread):
    log = logging.getLogger("nodepool.InstanceDeleter")

    def __init__(self, nodepool, provider_name, external_id):
        threading.Thread.__init__(self, name='InstanceDeleter for %s %s' %
                                  (provider_name, external_id))
        self.nodepool = nodepool
        self.provider_name = provider_name
        self.external_id = external_id

    def run(self):
        try:
            self.nodepool._deleteInstance(self.provider_name,
                                          self.external_id)
        except Exception:
            self.log.exception("Exception deleting instance %s from %s:" %
                               (self.external_id, self.provider_name))


class NodeDeleter(threading.Thread):
    log = logging.getLogger("nodepool.NodeDeleter")

    def __init__(self, nodepool, node_id):
        threading.Thread.__init__(self, name='NodeDeleter for %s' % node_id)
        self.node_id = node_id
        self.nodepool = nodepool

    def run(self):
        try:
            with self.nodepool.getDB().getSession() as session:
                node = session.getNode(self.node_id)
                self.nodepool._deleteNode(session, node)
        except Exception:
            self.log.exception("Exception deleting node %s:" %
                               self.node_id)


class NodeLauncher(threading.Thread):
    log = logging.getLogger("nodepool.NodeLauncher")

    def __init__(self, nodepool, provider, label, target, node_id, timeout,
                 launch_timeout):
        threading.Thread.__init__(self, name='NodeLauncher for %s' % node_id)
        self.provider = provider
        self.label = label
        self.image = provider.images[label.image]
        self.target = target
        self.node_id = node_id
        self.timeout = timeout
        self.nodepool = nodepool
        self.launch_timeout = launch_timeout

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in run method:")

    def _run(self):
        with self.nodepool.getDB().getSession() as session:
            self.log.debug("Launching node id: %s" % self.node_id)
            try:
                self.node = session.getNode(self.node_id)
                self.manager = self.nodepool.getProviderManager(self.provider)
            except Exception:
                self.log.exception("Exception preparing to launch node id: %s:"
                                   % self.node_id)
                return

            try:
                start_time = time.time()
                dt = self.launchNode(session)
                failed = False
                statsd_key = 'ready'
                self.log.debug('Node %s ready in provider: %s' %
                               (self.node_id, self.provider.name))
            except exceptions.TimeoutException as e:
                # Don't log exception for timeouts. Each one has
                # a specific Exception, and we know it's a timeout, so
                # the traceback in the log is just noise
                self.log.error("Timeout launching node id: %s "
                                   "in provider: %s error: %s" %
                                   (self.node_id, self.provider.name,
                                    str(e)))
                dt = int((time.time() - start_time) * 1000)
                failed = True
                statsd_key = e.statsd_key
            except Exception as e:
                self.log.exception("%s launching node id: %s "
                                   "in provider: %s error:" %
                                   (e.__class__.__name__,
                                    self.node_id, self.provider.name))
                dt = int((time.time() - start_time) * 1000)
                failed = True
                if hasattr(e, 'statsd_key'):
                    statsd_key = e.statsd_key
                else:
                    statsd_key = 'error.unknown'

            try:

                self.nodepool.launchStats(statsd_key, dt, self.image.name,
                                          self.provider.name,
                                          self.target.name,
                                          self.node.az,
                                          self.node.manager_name)
            except Exception:
                self.log.exception("Exception reporting launch stats:")

            if failed:
                try:
                    self.nodepool.deleteNode(self.node_id)
                except Exception:
                    self.log.exception("Exception deleting node id: %s:" %
                                       self.node_id)

    def launchNode(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        hostname = self.target.hostname.format(
            label=self.label, provider=self.provider, node_id=self.node.id,
            timestamp=str(timestamp))
        self.node.hostname = hostname
        self.node.nodename = hostname.split('.')[0]
        self.node.target_name = self.target.name

        snap_image = session.getCurrentSnapshotImage(
            self.provider.name, self.image.name)
        if not snap_image:
            raise LaunchNodepoolException("Unable to find current snapshot "
                                          "image %s in %s" %
                                          (self.image.name,
                                           self.provider.name))

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self.provider.name,
                                           self.image.name, self.node_id))
        server = self.manager.createServer(
            hostname, self.image.min_ram, snap_image.external_id,
            name_filter=self.image.name_filter, az=self.node.az,
            config_drive=self.image.config_drive,
            nodepool_node_id=self.node_id,
            nodepool_image_name=self.image.name)
        server_id = server['id']
        self.node.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server_id, self.node.id))
        server = self.manager.waitForServer(server, self.launch_timeout)
        if server['status'] != 'ACTIVE':
            raise LaunchStatusException("Server %s for node id: %s "
                                        "status: %s" %
                                        (server_id, self.node.id,
                                         server['status']))

        ip = server.get('public_v4')
        ip_v6 = server.get('public_v6')
        if self.provider.ipv6_preferred:
            if ip_v6:
                ip = ip_v6
            else:
                self.log.warning('Preferred ipv6 not available, '
                                 'falling back to ipv4.')
        if not ip:
            self.log.debug(
                "Server data for failed IP: %s" % pprint.pformat(
                    server))
            raise LaunchNetworkException("Unable to find public IP of server")

        self.node.ip_private = server.get('private_v4')
        # devstack-gate multi-node depends on private_v4 being populated
        # with something. On clouds that don't have a private address, use
        # the public.
        if not self.node.ip_private:
            self.node.ip_private = server.get('public_v4')
        self.node.ip = ip
        self.log.debug("Node id: %s is running, ipv4: %s, ipv6: %s" %
                       (self.node.id, server.get('public_v4'),
                        server.get('public_v6')))

        self.log.debug("Node id: %s testing ssh at ip: %s" %
                       (self.node.id, ip))
        connect_kwargs = dict(key_filename=self.image.private_key)
        if not utils.ssh_connect(ip, self.image.username,
                                 connect_kwargs=connect_kwargs,
                                 timeout=self.timeout):
            raise LaunchAuthException("Unable to connect via ssh")

        # Save the elapsed time for statsd
        dt = int((time.time() - start_time) * 1000)

        if self.label.subnodes:
            self.log.info("Node id: %s is waiting on subnodes" % self.node.id)

            while ((time.time() - start_time) < (NODE_CLEANUP - 60)):
                session.commit()
                ready_subnodes = [n for n in self.node.subnodes
                                  if n.state == nodedb.READY]
                if len(ready_subnodes) == self.label.subnodes:
                    break
                time.sleep(5)

        nodelist = []
        for subnode in self.node.subnodes:
            nodelist.append(('sub', subnode))
        nodelist.append(('primary', self.node))

        self.writeNodepoolInfo(nodelist)
        if self.label.ready_script:
            self.runReadyScript(nodelist)

        # Do this before adding to jenkins to avoid a race where
        # Jenkins might immediately use the node before we've updated
        # the state:
        if self.target.jenkins_test_job:
            self.node.state = nodedb.TEST
            self.log.info("Node id: %s is in testing" % self.node.id)
        else:
            self.node.state = nodedb.READY
            self.log.info("Node id: %s is ready" % self.node.id)
        self.nodepool.updateStats(session, self.provider.name)

        if self.target.jenkins_url:
            self.log.debug("Adding node id: %s to jenkins" % self.node.id)
            self.createJenkinsNode()
            self.log.info("Node id: %s added to jenkins" % self.node.id)

        if self.target.assign_via_gearman:
            self.log.info("Node id: %s assigning via gearman" % self.node.id)
            self.assignViaGearman()

        return dt

    def createJenkinsNode(self):
        jenkins = self.nodepool.getJenkinsManager(self.target)

        args = dict(name=self.node.nodename,
                    host=self.node.ip,
                    description='Dynamic single use %s node' % self.label.name,
                    executors=1,
                    root=self.image.user_home)
        if not self.target.jenkins_test_job:
            args['labels'] = self.label.name
        if self.target.jenkins_credentials_id:
            args['credentials_id'] = self.target.jenkins_credentials_id
        else:
            args['username'] = self.image.username
            args['private_key'] = self.image.private_key

        jenkins.createNode(**args)

        if self.target.jenkins_test_job:
            params = dict(NODE=self.node.nodename)
            jenkins.startBuild(self.target.jenkins_test_job, params)

    def assignViaGearman(self):
        args = dict(name=self.node.nodename,
                    host=self.node.ip,
                    description='Dynamic single use %s node' % self.label.name,
                    labels=self.label.name,
                    root=self.image.user_home)
        job = jobs.NodeAssignmentJob(self.node.id, self.node.target_name,
                                     args, self.nodepool)
        self.nodepool.gearman_client.submitJob(job, timeout=300)
        job.waitForCompletion()
        self.log.info("Node id: %s received %s from assignment" % (
            self.node.id, job.data))
        if job.failure:
            raise Exception("Node id: %s received job failure on assignment" %
                            self.node.id)
        data = json.loads(job.data[-1])
        self.node.manager_name = data['manager']

    def writeNodepoolInfo(self, nodelist):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()

        for role, n in nodelist:
            connect_kwargs = dict(key_filename=self.image.private_key)
            host = utils.ssh_connect(n.ip, self.image.username,
                                     connect_kwargs=connect_kwargs,
                                     timeout=self.timeout)
            if not host:
                raise Exception("Unable to log in via SSH")

            host.ssh("test for config dir", "ls /etc/nodepool")

            ftp = host.client.open_sftp()

            # The Role of this node
            f = ftp.open('/etc/nodepool/role', 'w')
            f.write(role + '\n')
            f.close()
            # The IP of this node
            f = ftp.open('/etc/nodepool/node', 'w')
            f.write(n.ip + '\n')
            f.close()
            # The private IP of this node
            f = ftp.open('/etc/nodepool/node_private', 'w')
            f.write(n.ip_private + '\n')
            f.close()
            # The IP of the primary node of this node set
            f = ftp.open('/etc/nodepool/primary_node', 'w')
            f.write(self.node.ip + '\n')
            f.close()
            # The private IP of the primary node of this node set
            f = ftp.open('/etc/nodepool/primary_node_private', 'w')
            f.write(self.node.ip_private + '\n')
            f.close()
            # The IPs of all sub nodes in this node set
            f = ftp.open('/etc/nodepool/sub_nodes', 'w')
            for subnode in self.node.subnodes:
                f.write(subnode.ip + '\n')
            f.close()
            # The private IPs of all sub nodes in this node set
            f = ftp.open('/etc/nodepool/sub_nodes_private', 'w')
            for subnode in self.node.subnodes:
                f.write(subnode.ip_private + '\n')
            f.close()
            # The SSH key for this node set
            f = ftp.open('/etc/nodepool/id_rsa', 'w')
            key.write_private_key(f)
            f.close()
            f = ftp.open('/etc/nodepool/id_rsa.pub', 'w')
            f.write(public_key)
            f.close()
            # Provider information for this node set
            f = ftp.open('/etc/nodepool/provider', 'w')
            f.write('NODEPOOL_PROVIDER=%s\n' % self.provider.name)
            f.write('NODEPOOL_CLOUD=%s\n' % self.provider.cloud_config.name)
            f.write('NODEPOOL_REGION=%s\n' % (
                self.provider.region_name or '',))
            f.write('NODEPOOL_AZ=%s\n' % (self.node.az or '',))
            f.close()
            # The instance UUID for this node
            f = ftp.open('/etc/nodepool/uuid', 'w')
            f.write(n.external_id + '\n')
            f.close()

            ftp.close()

    def runReadyScript(self, nodelist):
        for role, n in nodelist:
            connect_kwargs = dict(key_filename=self.image.private_key)
            host = utils.ssh_connect(n.ip, self.image.username,
                                     connect_kwargs=connect_kwargs,
                                     timeout=self.timeout)
            if not host:
                raise Exception("Unable to log in via SSH")

            env_vars = ''
            for k, v in os.environ.items():
                if k.startswith('NODEPOOL_'):
                    env_vars += ' %s="%s"' % (k, v)
            host.ssh("run ready script",
                     "cd /opt/nodepool-scripts && %s ./%s %s" %
                     (env_vars, self.label.ready_script, n.hostname),
                     output=True)


class SubNodeLauncher(threading.Thread):
    log = logging.getLogger("nodepool.SubNodeLauncher")

    def __init__(self, nodepool, provider, label, subnode_id,
                 node_id, node_target_name, timeout, launch_timeout, node_az,
                 manager_name):
        threading.Thread.__init__(self, name='SubNodeLauncher for %s'
                                  % subnode_id)
        self.provider = provider
        self.label = label
        self.image = provider.images[label.image]
        self.node_target_name = node_target_name
        self.subnode_id = subnode_id
        self.node_id = node_id
        self.timeout = timeout
        self.nodepool = nodepool
        self.launch_timeout = launch_timeout
        self.node_az = node_az
        self.manager_name = manager_name

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in run method:")

    def _run(self):
        with self.nodepool.getDB().getSession() as session:
            self.log.debug("Launching subnode id: %s for node id: %s" %
                           (self.subnode_id, self.node_id))
            try:
                self.subnode = session.getSubNode(self.subnode_id)
                self.manager = self.nodepool.getProviderManager(self.provider)
            except Exception:
                self.log.exception("Exception preparing to launch subnode "
                                   "id: %s for node id: %s:"
                                   % (self.subnode_id, self.node_id))
                return

            try:
                start_time = time.time()
                dt = self.launchSubNode(session)
                failed = False
                statsd_key = 'ready'
            except Exception as e:
                self.log.exception("%s launching subnode id: %s "
                                   "for node id: %s in provider: %s error:" %
                                   (e.__class__.__name__, self.subnode_id,
                                    self.node_id, self.provider.name))
                dt = int((time.time() - start_time) * 1000)
                failed = True
                if hasattr(e, 'statsd_key'):
                    statsd_key = e.statsd_key
                else:
                    statsd_key = 'error.unknown'

            try:
                self.nodepool.launchStats(statsd_key, dt, self.image.name,
                                          self.provider.name,
                                          self.node_target_name,
                                          self.node_az,
                                          self.manager_name)
            except Exception:
                self.log.exception("Exception reporting launch stats:")

            if failed:
                try:
                    self.nodepool.deleteSubNode(self.subnode, self.manager)
                except Exception:
                    self.log.exception("Exception deleting subnode id: %s: "
                                       "for node id: %s:" %
                                       (self.subnode_id, self.node_id))
                    return

    def launchSubNode(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        target = self.nodepool.config.targets[self.node_target_name]
        hostname = target.subnode_hostname.format(
            label=self.label, provider=self.provider, node_id=self.node_id,
            subnode_id=self.subnode_id, timestamp=str(timestamp))
        self.subnode.hostname = hostname
        self.subnode.nodename = hostname.split('.')[0]

        snap_image = session.getCurrentSnapshotImage(
            self.provider.name, self.image.name)
        if not snap_image:
            raise LaunchNodepoolException("Unable to find current snapshot "
                                          "image %s in %s" %
                                          (self.image.name,
                                           self.provider.name))

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for subnode id: %s for node id: %s"
                      % (hostname, self.provider.name,
                         self.image.name, self.subnode_id, self.node_id))
        server = self.manager.createServer(
            hostname, self.image.min_ram, snap_image.external_id,
            name_filter=self.image.name_filter, az=self.node_az,
            config_drive=self.image.config_drive,
            nodepool_node_id=self.node_id,
            nodepool_image_name=self.image.name)
        server_id = server['id']
        self.subnode.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for subnode id: %s for "
                       "node id: %s" %
                       (server_id, self.subnode_id, self.node_id))
        server = self.manager.waitForServer(server, self.launch_timeout)
        if server['status'] != 'ACTIVE':
            raise LaunchStatusException("Server %s for subnode id: "
                                        "%s for node id: %s "
                                        "status: %s" %
                                        (server_id, self.subnode_id,
                                         self.node_id, server['status']))

        ip = server.get('public_v4')
        ip_v6 = server.get('public_v6')
        if self.provider.ipv6_preferred:
            if ip_v6:
                ip = ip_v6
            else:
                self.log.warning('Preferred ipv6 not available, '
                                 'falling back to ipv4.')
        if not ip:
            raise LaunchNetworkException("Unable to find public IP of server")

        self.subnode.ip_private = server.get('private_v4')
        # devstack-gate multi-node depends on private_v4 being populated
        # with something. On clouds that don't have a private address, use
        # the public.
        if not self.subnode.ip_private:
            self.subnode.ip_private = server.get('public_v4')
        self.subnode.ip = ip
        self.log.debug("Subnode id: %s for node id: %s is running, "
                       "ipv4: %s, ipv6: %s" %
                       (self.subnode_id, self.node_id, server.get('public_v4'),
                        server.get('public_v6')))

        self.log.debug("Subnode id: %s for node id: %s testing ssh at ip: %s" %
                       (self.subnode_id, self.node_id, ip))
        connect_kwargs = dict(key_filename=self.image.private_key)
        if not utils.ssh_connect(ip, self.image.username,
                                 connect_kwargs=connect_kwargs,
                                 timeout=self.timeout):
            raise LaunchAuthException("Unable to connect via ssh")

        # Save the elapsed time for statsd
        dt = int((time.time() - start_time) * 1000)

        self.subnode.state = nodedb.READY
        self.log.info("Subnode id: %s for node id: %s is ready"
                      % (self.subnode_id, self.node_id))
        self.nodepool.updateStats(session, self.provider.name)

        return dt


class ImageDeleter(threading.Thread):
    log = logging.getLogger("nodepool.ImageDeleter")

    def __init__(self, nodepool, snap_image_id):
        threading.Thread.__init__(self,
                                  name='ImageDeleter for %s' % snap_image_id)
        self.snap_image_id = snap_image_id
        self.nodepool = nodepool

    def run(self):
        try:
            with self.nodepool.getDB().getSession() as session:
                snap_image = session.getSnapshotImage(self.snap_image_id)
                self.nodepool._deleteImage(session, snap_image)
        except Exception:
            self.log.exception("Exception deleting image %s:" %
                               self.snap_image_id)


class ImageUpdater(threading.Thread):
    log = logging.getLogger("nodepool.ImageUpdater")

    def __init__(self, nodepool, provider, image, snap_image_id):
        threading.Thread.__init__(self, name='ImageUpdater for %s' %
                                  snap_image_id)
        self.provider = provider
        self.image = image
        self.snap_image_id = snap_image_id
        self.nodepool = nodepool
        self.scriptdir = self.nodepool.config.scriptdir
        self.elementsdir = self.nodepool.config.elementsdir
        self.imagesdir = self.nodepool.config.imagesdir
        self.statsd = stats.get_client()

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in run method:")

    def _run(self):
        with self.nodepool.getDB().getSession() as session:
            self.log.debug("Updating image %s in %s " % (self.image.name,
                                                         self.provider.name))
            try:
                self.snap_image = session.getSnapshotImage(
                    self.snap_image_id)
                self.manager = self.nodepool.getProviderManager(self.provider)
            except Exception:
                self.log.exception("Exception preparing to update image %s "
                                   "in %s:" % (self.image.name,
                                               self.provider.name))
                return

            try:
                self.updateImage(session)
            except Exception:
                self.log.exception("Exception updating image %s in %s:" %
                                   (self.image.name, self.provider.name))
                try:
                    if self.snap_image:
                        self.nodepool.deleteImage(self.snap_image.id)
                except Exception:
                    self.log.exception("Exception deleting image id: %s:" %
                                       self.snap_image.id)
                    return


class SnapshotImageUpdater(ImageUpdater):

    log = logging.getLogger("nodepool.SnapshotImageUpdater")

    def updateImage(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        hostname = self.provider.template_hostname.format(
            provider=self.provider, image=self.image, timestamp=str(timestamp))
        self.log.info("Creating image id: %s with hostname %s for %s in %s" %
                      (self.snap_image.id, hostname, self.image.name,
                       self.provider.name))
        if self.provider.keypair:
            key_name = self.provider.keypair
            key = None
            use_password = False
        else:
            try:
                key_name = hostname.split('.')[0]
                key = self.manager.addKeypair(key_name)
                use_password = False
            except Exception:
                key_name = None
                key = None
                use_password = True

        uuid_pattern = 'hex{8}-(hex{4}-){3}hex{12}'.replace('hex',
                                                            '[0-9a-fA-F]')
        if re.match(uuid_pattern, self.image.base_image):
            image_name = None
            image_id = self.image.base_image
        else:
            image_name = self.image.base_image
            image_id = None
        try:
            server = self.manager.createServer(
                hostname, self.image.min_ram, image_name=image_name,
                key_name=key_name, name_filter=self.image.name_filter,
                image_id=image_id, config_drive=self.image.config_drive,
                nodepool_snapshot_image_id=self.snap_image.id)
            server_id = server['id']
        except Exception:
            if self.manager.deleteKeypair(key_name):
                # Only log success - failure is logged inside of shade
                self.log.debug(
                    'Deleted keypair for failed image build %s' %
                    self.snap_image.id)
            raise

        self.snap_image.hostname = hostname
        self.snap_image.version = timestamp
        self.snap_image.server_external_id = server_id
        session.commit()

        self.log.debug("Image id: %s waiting for server %s" %
                       (self.snap_image.id, server_id))
        server = self.manager.waitForServer(server)
        if server['status'] != 'ACTIVE':
            raise Exception("Server %s for image id: %s status: %s" %
                            (server_id, self.snap_image.id, server['status']))

        ip = server.get('public_v4')
        ip_v6 = server.get('public_v6')
        if self.provider.ipv6_preferred:
            if ip_v6:
                ip = ip_v6
            else:
                self.log.warning('Preferred ipv6 not available, '
                                 'falling back to ipv4.')
        if not ip:
            self.log.error("Server dict {server}".format(
                server=pprint.pformat(dict(server))))
            raise Exception("Unable to find public IP of server")
        server['public_ip'] = ip

        self.bootstrapServer(server, key, use_password=use_password)

        image_id = self.manager.createImage(server, hostname,
                                            self.image.meta)['id']
        self.snap_image.external_id = image_id
        session.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image_id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        image = self.manager.waitForImage(image_id, IMAGE_TIMEOUT)
        # Throw exception here and not in waitForImage so that we can log
        # the snap_image.id as well, which waitForImage does not know
        if image['status'].lower() != 'active':
            raise Exception("Image %s for image id: %s status: %s" %
                            (image_id, self.snap_image.id, image['status']))

        if self.statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (self.image.name,
                                                   self.provider.name)
            self.statsd.timing(key, dt)
            self.statsd.incr(key)

        self.snap_image.state = nodedb.READY
        session.commit()
        self.log.info("Image %s in %s is ready" % (hostname,
                                                   self.provider.name))

        try:
            # We made the snapshot, try deleting the server, but it's okay
            # if we fail.  The reap script will find it and try again.
            self.manager.cleanupServer(server_id)
            self.manager.waitForServerDeletion(server_id)
        except:
            self.log.exception("Exception encountered deleting server"
                               " %s for image id: %s" %
                               (server_id, self.snap_image.id))

    def bootstrapServer(self, server, key, use_password=False):
        log = logging.getLogger("nodepool.image.build.%s.%s" %
                                (self.provider.name, self.image.name))

        ssh_kwargs = dict(log=log)
        if not use_password:
            ssh_kwargs['pkey'] = key
        else:
            ssh_kwargs['password'] = server['admin_pass']

        host = utils.ssh_connect(server['public_ip'], 'root', ssh_kwargs,
                                 timeout=CONNECT_TIMEOUT)

        if not host:
            # We have connected to the node but couldn't do anything as root
            # try distro specific users, since we know ssh is up (a timeout
            # didn't occur), we can connect with a very sort timeout.
            for username in ['ubuntu', 'fedora', 'cloud-user', 'centos',
                             'debian']:
                try:
                    host = utils.ssh_connect(server['public_ip'], username,
                                             ssh_kwargs,
                                             timeout=10)
                    if host:
                        break
                except:
                    continue

        if not host:
            raise Exception("Unable to log in via SSH")

        # /etc/nodepool is world writable because by the time we write
        # the contents after the node is launched, we may not have
        # sudo access any more.
        host.ssh("make config dir", "sudo mkdir -p /etc/nodepool")
        host.ssh("chmod config dir", "sudo chmod 0777 /etc/nodepool")
        if self.scriptdir:
            host.ssh("make scripts dir", "mkdir -p scripts")
            for fname in os.listdir(self.scriptdir):
                path = os.path.join(self.scriptdir, fname)
                if not os.path.isfile(path):
                    continue
                host.scp(path, 'scripts/%s' % fname)
            host.ssh("move scripts to opt",
                     "sudo mv scripts /opt/nodepool-scripts")
            host.ssh("set scripts permissions",
                     "sudo chmod -R a+rx /opt/nodepool-scripts")

        if self.image.setup:
            env_vars = ''
            for k, v in os.environ.items():
                if k.startswith('NODEPOOL_'):
                    env_vars += ' %s="%s"' % (k, v)

            # non-interactive "cloud-user" type logins can have very
            # restrictive paths of just /bin:/usr/bin.  Because on
            # some hosts we log-in as root and others as a user, we
            # standarise the path here
            set_path = "export PATH=" \
                       "/usr/local/sbin:/sbin:/usr/sbin:" \
                       "/usr/local/bin:/bin:/usr/bin"
            host.ssh("run setup script",
                     "%s; cd /opt/nodepool-scripts "
                     "&& %s ./%s %s && sync && sleep 5" %
                     (set_path, env_vars, self.image.setup, server['name']))


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, securefile, configfile,
                 watermark_sleep=WATERMARK_SLEEP):
        threading.Thread.__init__(self, name='NodePool')
        self.securefile = securefile
        self.configfile = configfile
        self.watermark_sleep = watermark_sleep
        self._stopped = False
        self.config = None
        self.zmq_context = None
        self.gearman_client = None
        self.apsched = None
        self.statsd = stats.get_client()
        self._delete_threads = {}
        self._delete_threads_lock = threading.Lock()
        self._image_delete_threads = {}
        self._image_delete_threads_lock = threading.Lock()
        self._instance_delete_threads = {}
        self._instance_delete_threads_lock = threading.Lock()
        self._image_build_jobs = JobTracker()
        self._wake_condition = threading.Condition()

    def stop(self):
        self._stopped = True
        self._wake_condition.acquire()
        self._wake_condition.notify()
        self._wake_condition.release()
        if self.config:
            for z in self.config.zmq_publishers.values():
                z.listener.stop()
                z.listener.join()
            provider_manager.ProviderManager.stopProviders(self.config)
        if self.zmq_context:
            self.zmq_context.destroy()
        if self.apsched and self.apsched.running:
            self.apsched.shutdown()
        if self.gearman_client:
            self.gearman_client.shutdown()
        self.log.debug("finished stopping")

    def waitForBuiltImages(self):
        self.log.debug("Waiting for images to complete building.")
        for job in self._image_build_jobs.running_jobs:
            job.waitForCompletion()
        self.log.debug("Done waiting for images to complete building.")

    def loadConfig(self):
        self.log.debug("Loading configuration")
        config = nodepool_config.loadConfig(self.configfile)
        nodepool_config.loadSecureConfig(config, self.securefile)
        return config

    def reconfigureDatabase(self, config):
        if (not self.config) or config.dburi != self.config.dburi:
            config.db = nodedb.NodeDatabase(config.dburi)
        else:
            config.db = self.config.db

    def reconfigureManagers(self, config, check_targets=True):
        provider_manager.ProviderManager.reconfigure(self.config, config)

        stop_managers = []
        for t in config.targets.values():
            oldmanager = None
            if self.config:
                oldmanager = self.config.jenkins_managers.get(t.name)
            if oldmanager:
                if (t.jenkins_url != oldmanager.target.jenkins_url or
                    t.jenkins_user != oldmanager.target.jenkins_user or
                    t.jenkins_apikey != oldmanager.target.jenkins_apikey):
                    stop_managers.append(oldmanager)
                    oldmanager = None
            if oldmanager:
                config.jenkins_managers[t.name] = oldmanager
            elif t.jenkins_url:
                self.log.debug("Creating new JenkinsManager object "
                               "for %s" % t.name)
                config.jenkins_managers[t.name] = \
                    jenkins_manager.JenkinsManager(t)
                config.jenkins_managers[t.name].start()
        for oldmanager in stop_managers:
            oldmanager.stop()

        # only do it if we need to check for targets
        if check_targets:
            for t in config.targets.values():
                if t.jenkins_url:
                    try:
                        info = config.jenkins_managers[t.name].getInfo()
                        if info['quietingDown']:
                            self.log.info("Target %s is offline" % t.name)
                            t.online = False
                        else:
                            t.online = True
                    except Exception:
                        self.log.exception("Unable to check status of %s" %
                                           t.name)
                        t.online = False

    def reconfigureCrons(self, config):
        cron_map = {
            'image-update': self._doUpdateImages,
            'cleanup': self._doPeriodicCleanup,
            'check': self._doPeriodicCheck,
            }

        if not self.apsched:
            self.apsched = apscheduler.schedulers.background.BackgroundScheduler()
            self.apsched.start()

        for c in config.crons.values():
            if ((not self.config) or
                c.timespec != self.config.crons[c.name].timespec):
                if self.config and self.config.crons[c.name].job:
                    self.config.crons[c.name].job.remove()
                parts = c.timespec.split()
                if len(parts) > 5:
                    second = parts[5]
                else:
                    second = None
                minute, hour, dom, month, dow = parts[:5]
                trigger = apscheduler.triggers.cron.CronTrigger(
                    day=dom, day_of_week=dow, hour=hour, minute=minute,
                    second=second)
                c.job = self.apsched.add_job(
                    cron_map[c.name], trigger=trigger)
            else:
                c.job = self.config.crons[c.name].job

    def reconfigureUpdateListeners(self, config):
        if self.config:
            running = set(self.config.zmq_publishers.keys())
        else:
            running = set()

        configured = set(config.zmq_publishers.keys())
        if running == configured:
            self.log.debug("ZMQ Listeners do not need to be updated")
            if self.config:
                config.zmq_publishers = self.config.zmq_publishers
            return

        if self.zmq_context:
            self.log.debug("Stopping listeners")
            self.zmq_context.destroy()
        self.zmq_context = zmq.Context()
        for z in config.zmq_publishers.values():
            self.log.debug("Starting listener for %s" % z.name)
            z.listener = NodeUpdateListener(self, z.name)
            z.listener.start()

    def reconfigureGearmanClient(self, config):
        if self.config:
            running = set(self.config.gearman_servers.keys())
        else:
            running = set()

        configured = set(config.gearman_servers.keys())
        if running == configured:
            self.log.debug("Gearman client does not need to be updated")
            if self.config:
                config.gearman_servers = self.config.gearman_servers
            return

        if self.gearman_client:
            self.log.debug("Stopping gearman client")
            self.gearman_client.shutdown()
            self.gearman_client = None
        if configured:
            self.gearman_client = GearmanClient()
            for g in config.gearman_servers.values():
                self.log.debug("Adding gearman server %s" % g.name)
                self.gearman_client.addServer(g.host, g.port)
            self.gearman_client.waitForServer()

    def setConfig(self, config):
        self.config = config

    def getDB(self):
        return self.config.db

    def getProviderManager(self, provider):
        return self.config.provider_managers[provider.name]

    def getJenkinsManager(self, target):
        if target.name in self.config.jenkins_managers:
            return self.config.jenkins_managers[target.name]
        else:
            raise KeyError("{0} not in {1}".format(target.name,
                           self.config.jenkins_managers.keys()))

    def getNeededNodes(self, session, allocation_history):
        self.log.debug("Beginning node launch calculation")
        # Get the current demand for nodes.
        if self.gearman_client:
            label_demand = self.gearman_client.getNeededWorkers()
        else:
            label_demand = {}

        for name, demand in label_demand.items():
            self.log.debug("  Demand from gearman: %s: %s" % (name, demand))

        online_targets = set()
        for target in self.config.targets.values():
            if not target.online:
                continue
            online_targets.add(target.name)

        nodes = session.getNodes()

        def count_nodes(label_name, state):
            return len([n for n in nodes
                        if (n.target_name in online_targets and
                            n.label_name == label_name and
                            n.state == state)])

        def count_nodes_and_subnodes(provider_name):
            count = 0
            for n in nodes:
                if n.provider_name != provider_name:
                    continue
                count += 1 + len(n.subnodes)
            return count

        # Add a provider for each node provider, along with current
        # capacity
        allocation_providers = {}
        for provider in self.config.providers.values():
            provider_max = provider.max_servers
            n_provider = count_nodes_and_subnodes(provider.name)
            available = provider_max - n_provider
            if available < 0:
                self.log.warning("Provider %s over-allocated: "
                                 "max-servers %d but counted %d " %
                                 (provider.name, provider_max, n_provider))
                available = 0
            ap = allocation.AllocationProvider(provider.name, available)
            allocation_providers[provider.name] = ap

        # calculate demand for labels
        # Actual need is: demand - (ready + building + used)
        # NOTE(jhesketh): This assumes that the nodes in use are actually being
        # used for a job in demand.
        for label in self.config.labels.values():
            start_demand = label_demand.get(label.name, 0)
            n_ready = count_nodes(label.name, nodedb.READY)
            n_building = count_nodes(label.name, nodedb.BUILDING)
            n_used = count_nodes(label.name, nodedb.USED)
            n_test = count_nodes(label.name, nodedb.TEST)
            ready = n_ready + n_building + n_used + n_test

            capacity = 0
            for provider in label.providers.values():
                capacity += allocation_providers[provider.name].available

            # Note actual_demand and extra_demand are written this way
            # because max(0, x - y + z) != max(0, x - y) + z.
            # The requested number of nodes minus those already available
            actual_demand = max(0, start_demand - ready)
            # Demand that accomodates extra demand from min-ready value
            extra_demand = max(0, start_demand + label.min_ready - ready)
            # We only request extras for the min ready value if there is
            # clearly capacity for them. This is to avoid the allocator
            # making bad choices spinning up nodes to satisfy min-ready when
            # there is "real" work to do with other nodes.
            if extra_demand <= capacity:
                demand = extra_demand
            else:
                demand = actual_demand

            label_demand[label.name] = demand
            self.log.debug("  Deficit: %s: %s "
                           "(start: %s min-ready: %s ready: %s capacity: %s)" %
                           (label.name, demand,
                            start_demand, label.min_ready, ready, capacity))

        # "Target-Label-Provider" -- the triplet of info that identifies
        # the source and location of each node.  The mapping is
        # AllocationGrantTarget -> TargetLabelProvider, because
        # the allocation system produces AGTs as the final product.
        tlps = {}
        # label_name -> AllocationRequest
        allocation_requests = {}
        # Set up the request values in the allocation system
        for target in self.config.targets.values():
            if not target.online:
                continue
            at = allocation.AllocationTarget(target.name)
            for label in self.config.labels.values():
                ar = allocation_requests.get(label.name)
                if not ar:
                    # A request for a certain number of nodes of this
                    # label type.  We may have already started a
                    # request from a previous target-label in this
                    # loop.
                    ar = allocation.AllocationRequest(label.name,
                                                      label_demand[label.name],
                                                      allocation_history)

                nodes = session.getNodes(label_name=label.name,
                                         target_name=target.name)
                allocation_requests[label.name] = ar
                ar.addTarget(at, len(nodes))
                for provider in label.providers.values():
                    image = session.getCurrentSnapshotImage(
                        provider.name, label.image)
                    if image:
                        # This request may be supplied by this provider
                        # (and nodes from this provider supplying this
                        # request should be distributed to this target).
                        sr, agt = ar.addProvider(
                            allocation_providers[provider.name],
                            at, label.subnodes)
                        tlps[agt] = (target, label,
                                     self.config.providers[provider.name])
                    else:
                        self.log.debug("  %s does not have image %s "
                                       "for label %s." % (provider.name,
                                                          label.image,
                                                          label.name))

        self.log.debug("  Allocation requests:")
        for ar in allocation_requests.values():
            self.log.debug('    %s' % ar)
            for sr in ar.sub_requests.values():
                self.log.debug('      %s' % sr)

        nodes_to_launch = []

        # Let the allocation system do it's thing, and then examine
        # the AGT objects that it produces.
        self.log.debug("  Grants:")
        for ap in allocation_providers.values():
            ap.makeGrants()
            for g in ap.grants:
                self.log.debug('    %s' % g)
                for agt in g.targets:
                    self.log.debug('      %s' % agt)
                    tlp = tlps[agt]
                    nodes_to_launch.append((tlp, agt.amount))

        allocation_history.grantsDone()

        self.log.debug("Finished node launch calculation")
        return nodes_to_launch

    def getNeededSubNodes(self, session):
        nodes_to_launch = []
        for node in session.getNodes():
            if node.label_name in self.config.labels:
                expected_subnodes = \
                    self.config.labels[node.label_name].subnodes
                active_subnodes = len([n for n in node.subnodes
                                       if n.state != nodedb.DELETE])
                deficit = max(expected_subnodes - active_subnodes, 0)
                if deficit:
                    nodes_to_launch.append((node, deficit))
        return nodes_to_launch

    def updateConfig(self):
        config = self.loadConfig()
        self.reconfigureDatabase(config)
        self.reconfigureManagers(config)
        self.reconfigureUpdateListeners(config)
        self.reconfigureGearmanClient(config)
        self.reconfigureCrons(config)
        self.setConfig(config)

    def startup(self):
        self.updateConfig()

        # Currently nodepool can not resume building a node or image
        # after a restart.  To clean up, mark all building node and
        # images for deletion when the daemon starts.
        with self.getDB().getSession() as session:
            for node in session.getNodes(state=nodedb.BUILDING):
                self.log.info("Setting building node id: %s to delete "
                              "on startup" % node.id)
                node.state = nodedb.DELETE

            for image in session.getSnapshotImages(state=nodedb.BUILDING):
                self.log.info("Setting building image id: %s to delete "
                              "on startup" % image.id)
                image.state = nodedb.DELETE

            for dib_image in session.getDibImages(state=nodedb.BUILDING):
                self.log.info("Setting building dib image id: %s to delete "
                              "on startup" % dib_image.id)
                dib_image.state = nodedb.DELETE

    def run(self):
        try:
            self.startup()
        except Exception:
            self.log.exception("Exception in startup:")
        allocation_history = allocation.AllocationHistory()
        while not self._stopped:
            try:
                self.updateConfig()
                with self.getDB().getSession() as session:
                    self._run(session, allocation_history)
            except Exception:
                self.log.exception("Exception in main loop:")
            self._wake_condition.acquire()
            self._wake_condition.wait(self.watermark_sleep)
            self._wake_condition.release()

    def _run(self, session, allocation_history):
        self.checkForMissingImages(session)

        # Make up the subnode deficit first to make sure that an
        # already allocated node has priority in filling its subnodes
        # ahead of new nodes.
        subnodes_to_launch = self.getNeededSubNodes(session)
        for (node, num_to_launch) in subnodes_to_launch:
            self.log.info("Need to launch %s subnodes for node id: %s" %
                          (num_to_launch, node.id))
            for i in range(num_to_launch):
                self.launchSubNode(session, node)

        nodes_to_launch = self.getNeededNodes(session, allocation_history)

        for (tlp, num_to_launch) in nodes_to_launch:
            (target, label, provider) = tlp
            if (not target.online) or (not num_to_launch):
                continue
            self.log.info("Need to launch %s %s nodes for %s on %s" %
                          (num_to_launch, label.name,
                           target.name, provider.name))
            for i in range(num_to_launch):
                snap_image = session.getCurrentSnapshotImage(
                    provider.name, label.image)
                if not snap_image:
                    self.log.debug("No current image for %s on %s"
                                   % (label.image, provider.name))
                else:
                    self.launchNode(session, provider, label, target)

    def checkForMissingSnapshotImage(self, session, provider, image):
        found = False
        for snap_image in session.getSnapshotImages():
            if (snap_image.provider_name == provider.name and
                snap_image.image_name == image.name and
                snap_image.state in [nodedb.READY,
                                     nodedb.BUILDING]):
                found = True
        if not found:
            self.log.warning("Missing image %s on %s" %
                             (image.name, provider.name))
            self.updateImage(session, provider.name, image.name)

    def checkForMissingDiskImage(self, session, provider, image):
        found = False
        for dib_image in session.getDibImages():
            if dib_image.image_name != image.diskimage:
                continue
            if dib_image.state != nodedb.READY:
                # This is either building or in an error state
                # that will be handled by periodic cleanup
                return
            found = True
        if not found:
            # only build the image, we'll recheck again
            self.log.warning("Missing disk image %s" % image.name)
            self.buildImage(self.config.diskimages[image.diskimage])
        else:
            found = False
            for snap_image in session.getSnapshotImages():
                if (snap_image.provider_name == provider.name and
                    snap_image.image_name == image.name and
                    snap_image.state in [nodedb.READY,
                                         nodedb.BUILDING]):
                    found = True
                    break
            if not found:
                self.log.warning("Missing image %s on %s" %
                                 (image.name, provider.name))
                self.uploadImage(session, provider.name,
                                 image.name)

    def checkForMissingImage(self, session, provider, image):
        if image.name in self.config.images_in_use:
            if not image.diskimage:
                self.checkForMissingSnapshotImage(session, provider, image)
            else:
                self.checkForMissingDiskImage(session, provider, image)

    def checkForMissingImages(self, session):
        # If we are missing an image, run the image update function
        # outside of its schedule.
        self.log.debug("Checking missing images.")

        # this is sorted so we can have a deterministic pass on providers
        # (very useful for testing/debugging)
        providers = sorted(self.config.providers.values(),
                           key=lambda x: x.name)
        for provider in providers:
            for image in provider.images.values():
                try:
                    self.checkForMissingImage(session, provider, image)
                except Exception:
                    self.log.exception("Exception in missing image check:")

    def _doUpdateImages(self):
        try:
            with self.getDB().getSession() as session:
                self.updateImages(session)
        except Exception:
            self.log.exception("Exception in periodic image update:")

    def updateImages(self, session):
        self.log.debug("Updating all images.")

        # first run the snapshot image updates
        for provider in self.config.providers.values():
            for image in provider.images.values():
                if image.name not in self.config.images_in_use:
                    continue
                if image.diskimage:
                    continue
                self.updateImage(session, provider.name, image.name)

        needs_build = False
        for diskimage in self.config.diskimages.values():
            if diskimage.name not in self.config.images_in_use:
                continue
            self.buildImage(diskimage)
            needs_build = True
        if needs_build:
            # wait for all builds to finish, to have updated images to upload
            self.waitForBuiltImages()

        for provider in self.config.providers.values():
            for image in provider.images.values():
                if image.name not in self.config.images_in_use:
                    continue
                if not image.diskimage:
                    continue
                self.uploadImage(session, provider.name, image.name)

    def updateImage(self, session, provider_name, image_name):
        try:
            return self._updateImage(session, provider_name, image_name)
        except Exception:
            self.log.exception(
                "Could not update image %s on %s", image_name, provider_name)

    def _updateImage(self, session, provider_name, image_name):
        provider = self.config.providers[provider_name]
        image = provider.images[image_name]
        # check type of image depending on diskimage flag
        if image.diskimage:
            raise Exception(
                "Cannot update disk image images. "
                "Please build and upload images")

        if not image.setup:
            raise Exception(
                "Invalid image config. Must specify either "
                "a setup script, or a diskimage to use.")
        snap_image = session.createSnapshotImage(
            provider_name=provider.name,
            image_name=image_name)

        t = SnapshotImageUpdater(self, provider, image, snap_image.id)
        t.start()
        # Enough time to give them different timestamps (versions)
        # Just to keep things clearer.
        time.sleep(2)
        return t

    def buildImage(self, image):
        # check if we already have this item in the queue
        with self.getDB().getSession() as session:
            queued_images = session.getBuildingDibImagesByName(image.name)
            if queued_images:
                self.log.error('Image %s is already being built' %
                               image.name)
                return
            else:
                try:
                    start_time = time.time()
                    timestamp = int(start_time)

                    filename = os.path.join(self.config.imagesdir,
                                            '%s-%s' %
                                            (image.name, str(timestamp)))
                    dib_image = session.createDibImage(image_name=image.name,
                                                       filename=filename,
                                                       version=timestamp)
                    self.log.debug("Created DibImage record %s with state %s",
                                   dib_image.image_name, dib_image.state)

                    # Submit image-build job
                    gearman_job = jobs.ImageBuildJob(image.name, dib_image.id,
                                                     self)
                    self._image_build_jobs.addJob(gearman_job)

                    try:
                        self.gearman_client.submitJob(gearman_job, timeout=300)
                    except Exception:
                        self.log.warning("Failed to submit build job for "
                                         "DibImage record %s with id %d",
                                         dib_image.image_name, dib_image.id)
                        # Remove job from _image_build_jobs
                        gearman_job.onFailed()
                        raise
                    else:
                        self.log.debug("Queued image building task for %s" %
                                       image.name)
                except Exception:
                    self.log.exception(
                        "Could not build image %s", image.name)

    def uploadImage(self, session, provider, image_name):
        try:
            provider_entity = self.config.providers[provider]
            provider_image = provider_entity.images[image_name]
            images = session.getOrderedReadyDibImages(provider_image.diskimage)
            image_id = images[0].id
            timestamp = int(time.time())
            job_uuid = str(uuid4().hex)

            snap_image = session.createSnapshotImage(
                provider_name=provider, image_name=provider_image.name)
            self.log.debug('Created snapshot image id: %s for upload of '
                           'DIB image id: %s with job uuid: %s ' %
                           (snap_image.id, image_id, job_uuid))

            # TODO(mordred) abusing the hostname field
            snap_image.hostname = image_name
            snap_image.version = timestamp
            session.commit()

            # Submit image-upload job
            gearman_job = jobs.ImageUploadJob(image_id, provider, image_name,
                                              snap_image.id, self)
            self.log.debug('Submitting image-upload job uuid: %s' %
                           (gearman_job.unique,))
            self.gearman_client.submitJob(gearman_job, timeout=300)

            return gearman_job
        except Exception:
            self.log.exception(
                "Could not upload image %s on %s", image_name, provider)

    def launchNode(self, session, provider, label, target):
        try:
            self._launchNode(session, provider, label, target)
        except Exception:
            self.log.exception(
                "Could not launch node %s on %s", label.name, provider.name)

    def _launchNode(self, session, provider, label, target):
        provider = self.config.providers[provider.name]
        timeout = provider.boot_timeout
        launch_timeout = provider.launch_timeout
        if provider.azs:
            az = random.choice(provider.azs)
        else:
            az = None
        node = session.createNode(provider.name, label.name, target.name, az)
        t = NodeLauncher(self, provider, label, target, node.id, timeout,
                         launch_timeout)
        t.start()

    def launchSubNode(self, session, node):
        try:
            self._launchSubNode(session, node)
        except Exception:
            self.log.exception(
                "Could not launch subnode for node id: %s", node.id)

    def _launchSubNode(self, session, node):
        provider = self.config.providers[node.provider_name]
        label = self.config.labels[node.label_name]
        timeout = provider.boot_timeout
        launch_timeout = provider.launch_timeout
        subnode = session.createSubNode(node)
        t = SubNodeLauncher(self, provider, label, subnode.id,
                            node.id, node.target_name, timeout, launch_timeout,
                            node_az=node.az, manager_name=node.manager_name)
        t.start()

    def deleteSubNode(self, subnode, manager):
        # Don't try too hard here, the actual node deletion will make
        # sure this is cleaned up.
        if subnode.external_id:
            try:
                self.log.debug('Deleting server %s for subnode id: '
                               '%s of node id: %s' %
                               (subnode.external_id, subnode.id,
                                subnode.node.id))
                manager.cleanupServer(subnode.external_id)
                manager.waitForServerDeletion(subnode.external_id)
            except provider_manager.NotFound:
                pass
        subnode.delete()

    def deleteNode(self, node_id):
        try:
            self._delete_threads_lock.acquire()
            if node_id in self._delete_threads:
                return
            t = NodeDeleter(self, node_id)
            self._delete_threads[node_id] = t
            t.start()
        except Exception:
            self.log.exception("Could not delete node %s", node_id)
        finally:
            self._delete_threads_lock.release()

    def revokeAssignedNode(self, node):
        args = dict(name=node.nodename)
        job = jobs.NodeRevokeJob(node.id, node.manager_name,
                                 args, self)
        self.gearman_client.submitJob(job, timeout=300)
        # Do not wait for completion in case the manager is offline

    def _deleteNode(self, session, node):
        self.log.debug("Deleting node id: %s which has been in %s "
                       "state for %s hours" %
                       (node.id, nodedb.STATE_NAMES[node.state],
                        (time.time() - node.state_time) / (60 * 60)))
        # Delete a node
        if node.state != nodedb.DELETE:
            # Don't write to the session if not needed.
            node.state = nodedb.DELETE
        self.updateStats(session, node.provider_name)
        provider = self.config.providers[node.provider_name]
        target = self.config.targets[node.target_name]
        label = self.config.labels.get(node.label_name, None)
        if label and label.image in provider.images:
            image_name = provider.images[label.image].name
        else:
            image_name = None
        manager = self.getProviderManager(provider)

        if target.jenkins_url and (node.nodename is not None):
            jenkins = self.getJenkinsManager(target)
            jenkins_name = node.nodename
            if jenkins.nodeExists(jenkins_name):
                jenkins.deleteNode(jenkins_name)
            self.log.info("Deleted jenkins node id: %s" % node.id)

        if node.manager_name is not None:
            try:
                self.revokeAssignedNode(node)
            except Exception:
                self.log.exception("Exception revoking node id: %s" %
                                   node.id)

        for subnode in node.subnodes:
            if subnode.external_id:
                try:
                    self.log.debug('Deleting server %s for subnode id: '
                                   '%s of node id: %s' %
                                   (subnode.external_id, subnode.id, node.id))
                    manager.cleanupServer(subnode.external_id)
                except provider_manager.NotFound:
                    pass

        if node.external_id:
            try:
                self.log.debug('Deleting server %s for node id: %s' %
                               (node.external_id, node.id))
                manager.cleanupServer(node.external_id)
                manager.waitForServerDeletion(node.external_id)
            except provider_manager.NotFound:
                pass
            node.external_id = None

        for subnode in node.subnodes:
            if subnode.external_id:
                manager.waitForServerDeletion(subnode.external_id)
                subnode.delete()

        node.delete()
        self.log.info("Deleted node id: %s" % node.id)

        if self.statsd:
            dt = int((time.time() - node.state_time) * 1000)
            key = 'nodepool.delete.%s.%s.%s' % (image_name,
                                                node.provider_name,
                                                node.target_name)
            self.statsd.timing(key, dt)
            self.statsd.incr(key)
        self.updateStats(session, node.provider_name)

    def deleteImage(self, snap_image_id):
        try:
            self._image_delete_threads_lock.acquire()

            snap_image = None
            with self.getDB().getSession() as session:
                snap_image = session.getSnapshotImage(snap_image_id)
            if snap_image is None:
                self.log.error("No image '%s' found.", snap_image_id)
                return

            if snap_image_id in self._image_delete_threads:
                return
            t = ImageDeleter(self, snap_image_id)
            self._image_delete_threads[snap_image_id] = t
            t.start()
            return t
        except Exception:
            self.log.exception("Could not delete image %s", snap_image_id)
        finally:
            self._image_delete_threads_lock.release()

    def _deleteImage(self, session, snap_image):
        # Delete an image (and its associated server)
        snap_image.state = nodedb.DELETE
        provider = self.config.providers[snap_image.provider_name]
        manager = self.getProviderManager(provider)

        if snap_image.server_external_id:
            try:
                server = manager.getServer(snap_image.server_external_id)
                if server:
                    self.log.debug('Deleting server %s for image id: %s' %
                                   (snap_image.server_external_id,
                                    snap_image.id))
                    manager.cleanupServer(server['id'])
                    manager.waitForServerDeletion(server['id'])
                else:
                    raise provider_manager.NotFound
            except provider_manager.NotFound:
                self.log.warning('Image server id %s not found' %
                                 snap_image.server_external_id)

        if snap_image.external_id:
            remote_image = manager.getImage(snap_image.external_id)
            if remote_image is None:
                self.log.warning('Image id %s not found' %
                                 snap_image.external_id)
            else:
                self.log.debug('Deleting image %s' % remote_image['id'])
                manager.deleteImage(remote_image['id'])

        snap_image.delete()
        self.log.info("Deleted image id: %s" % snap_image.id)

    def deleteDibImage(self, dib_image):
        try:
            # Submit image-delete job
            gearman_job = jobs.ImageDeleteJob(dib_image.id, self)
            self.gearman_client.submitJob(gearman_job, timeout=300)
            dib_image.delete()
            return gearman_job
        except Exception:
            self.log.exception('Could not submit delete job for image id %s',
                               dib_image.id)

    def deleteInstance(self, provider_name, external_id):
        key = (provider_name, external_id)
        try:
            self._instance_delete_threads_lock.acquire()
            if key in self._instance_delete_threads:
                return
            t = InstanceDeleter(self, provider_name, external_id)
            self._instance_delete_threads[key] = t
            t.start()
        except Exception:
            self.log.exception("Could not delete instance %s on provider %s",
                               provider_name, external_id)
        finally:
            self._instance_delete_threads_lock.release()

    def _deleteInstance(self, provider_name, external_id):
        provider = self.config.providers[provider_name]
        manager = self.getProviderManager(provider)
        manager.cleanupServer(external_id)

    def _doPeriodicCleanup(self):
        try:
            self.periodicCleanup()
        except Exception:
            self.log.exception("Exception in periodic cleanup:")

    def periodicCleanup(self):
        # This function should be run periodically to clean up any hosts
        # that may have slipped through the cracks, as well as to remove
        # old images.

        self.log.debug("Starting periodic cleanup")

        for k, t in self._delete_threads.items()[:]:
            if not t.isAlive():
                del self._delete_threads[k]

        for k, t in self._image_delete_threads.items()[:]:
            if not t.isAlive():
                del self._image_delete_threads[k]

        for k, t in self._instance_delete_threads.items()[:]:
            if not t.isAlive():
                del self._instance_delete_threads[k]

        node_ids = []
        image_ids = []
        dib_image_ids = []
        with self.getDB().getSession() as session:
            for node in session.getNodes():
                node_ids.append(node.id)
            for image in session.getSnapshotImages():
                image_ids.append(image.id)
            for dib_image in session.getDibImages():
                dib_image_ids.append(dib_image.id)

        for node_id in node_ids:
            try:
                with self.getDB().getSession() as session:
                    node = session.getNode(node_id)
                    if node:
                        self.cleanupOneNode(session, node)
            except Exception:
                self.log.exception("Exception cleaning up node id %s:" %
                                   node_id)

        for image_id in image_ids:
            try:
                with self.getDB().getSession() as session:
                    image = session.getSnapshotImage(image_id)
                    self.cleanupOneImage(session, image)
            except Exception:
                self.log.exception("Exception cleaning up image id %s:" %
                                   image_id)

        for dib_image_id in dib_image_ids:
            try:
                with self.getDB().getSession() as session:
                    dib_image = session.getDibImage(dib_image_id)
                    if dib_image:
                        self.cleanupOneDibImage(session, dib_image)
            except Exception:
                self.log.exception("Exception cleaning up image id %s:" %
                                   dib_image_id)

        try:
            self.cleanupLeakedInstances()
            pass
        except Exception:
            self.log.exception("Exception cleaning up leaked nodes")

        self.log.debug("Finished periodic cleanup")

    def cleanupLeakedInstances(self):
        known_providers = self.config.providers.keys()
        for provider in self.config.providers.values():
            manager = self.getProviderManager(provider)
            servers = manager.listServers()
            with self.getDB().getSession() as session:
                for server in servers:
                    meta = server.get('metadata', {}).get('nodepool')
                    if not meta:
                        self.log.debug("Instance %s (%s) in %s has no "
                                       "nodepool metadata" % (
                                           server['name'], server['id'],
                                           provider.name))
                        continue
                    meta = json.loads(meta)
                    if meta['provider_name'] not in known_providers:
                        self.log.debug("Instance %s (%s) in %s "
                                       "lists unknown provider %s" % (
                                           server['name'], server['id'],
                                           provider.name,
                                           meta['provider_name']))
                        continue
                    snap_image_id = meta.get('snapshot_image_id')
                    node_id = meta.get('node_id')
                    if snap_image_id:
                        if session.getSnapshotImage(snap_image_id):
                            continue
                        self.log.warning("Deleting leaked instance %s (%s) "
                                         "in %s for snapshot image id: %s" % (
                                             server['name'], server['id'],
                                             provider.name,
                                             snap_image_id))
                        self.deleteInstance(provider.name, server['id'])
                    elif node_id:
                        if session.getNode(node_id):
                            continue
                        self.log.warning("Deleting leaked instance %s (%s) "
                                         "in %s for node id: %s" % (
                                             server['name'], server['id'],
                                             provider.name, node_id))
                        self.deleteInstance(provider.name, server['id'])
                    else:
                        self.log.warning("Instance %s (%s) in %s has no "
                                         "database id" % (
                                             server['name'], server['id'],
                                             provider.name))
                        continue
            if provider.clean_floating_ips:
                manager.cleanupLeakedFloaters()

    def cleanupOneNode(self, session, node):
        now = time.time()
        time_in_state = now - node.state_time
        if (node.state in [nodedb.READY, nodedb.HOLD]):
            return
        delete = False
        if (node.state == nodedb.DELETE):
            delete = True
        elif (node.state == nodedb.TEST and
              time_in_state > TEST_CLEANUP):
            delete = True
        elif time_in_state > NODE_CLEANUP:
            delete = True
        if delete:
            try:
                self.deleteNode(node.id)
            except Exception:
                self.log.exception("Exception deleting node id: "
                                   "%s" % node.id)

    def cleanupOneImage(self, session, image):
        # Normally, reap images that have sat in their current state
        # for 8 hours, unless the image is the current or previous
        # snapshot.
        delete = False
        now = time.time()
        if image.provider_name not in self.config.providers:
            delete = True
            self.log.info("Deleting image id: %s which has no current "
                          "provider" % image.id)
        elif (image.image_name not in
              self.config.providers[image.provider_name].images):
            delete = True
            self.log.info("Deleting image id: %s which has no current "
                          "base image" % image.id)
        elif (image.state == nodedb.DELETE):
            delete = True
            self.log.info("Deleting image id: %s which is in delete state "
                          % image.id)
        else:
            images = session.getOrderedReadySnapshotImages(
                image.provider_name, image.image_name)
            current = previous = None
            if len(images) > 0:
                current = images[0]
            if len(images) > 1:
                previous = images[1]
            if (image != current and image != previous and
                    (now - image.state_time) > IMAGE_CLEANUP):
                self.log.info("Deleting image id: %s which is "
                              "%s hours old" %
                              (image.id,
                               (now - image.state_time) / (60 * 60)))
                delete = True
        if delete:
            try:
                self.deleteImage(image.id)
            except Exception:
                self.log.exception("Exception deleting image id: %s:" %
                                   image.id)

    def cleanupOneDibImage(self, session, image):
        delete = False
        now = time.time()
        if (image.image_name not in self.config.diskimages):
            delete = True
            self.log.info("Deleting image id: %s which has no current "
                          "base image" % image.id)
        else:
            images = session.getOrderedReadyDibImages(
                image.image_name)
            current = previous = None
            if len(images) > 0:
                current = images[0]
            if len(images) > 1:
                previous = images[1]
            if (image != current and image != previous and
                    (now - image.state_time) > IMAGE_CLEANUP):
                self.log.info("Deleting image id: %s which is "
                              "%s hours old" %
                              (image.id,
                               (now - image.state_time) / (60 * 60)))
                delete = True
            if image.state == nodedb.DELETE:
                delete = True
        if delete:
            try:
                self.deleteDibImage(image)
            except Exception:
                self.log.exception("Exception deleting image id: %s:" %
                                   image.id)

    def _doPeriodicCheck(self):
        try:
            with self.getDB().getSession() as session:
                self.periodicCheck(session)
        except Exception:
            self.log.exception("Exception in periodic check:")

    def periodicCheck(self, session):
        # This function should be run periodically to make sure we can
        # still access hosts via ssh.

        self.log.debug("Starting periodic check")
        for node in session.getNodes():
            if node.state != nodedb.READY:
                continue
            provider = self.config.providers[node.provider_name]
            if node.label_name in self.config.labels:
                label = self.config.labels[node.label_name]
                image = provider.images[label.image]
                connect_kwargs = dict(key_filename=image.private_key)
                try:
                    if utils.ssh_connect(node.ip, image.username,
                                         connect_kwargs=connect_kwargs):
                        continue
                except Exception:
                    self.log.exception("SSH Check failed for node id: %s" %
                                       node.id)
            else:
                self.log.exception("Node with non-existing label %s" %
                                   node.label_name)
            self.deleteNode(node.id)
        self.log.debug("Finished periodic check")

    def updateStats(self, session, provider_name):
        if not self.statsd:
            return
        # This may be called outside of the main thread.

        states = {}

        #nodepool.nodes.STATE
        #nodepool.target.TARGET.nodes.STATE
        #nodepool.label.LABEL.nodes.STATE
        #nodepool.provider.PROVIDER.nodes.STATE
        for state in nodedb.STATE_NAMES.values():
            key = 'nodepool.nodes.%s' % state
            states[key] = 0
            for target in self.config.targets.values():
                key = 'nodepool.target.%s.nodes.%s' % (
                    target.name, state)
                states[key] = 0
            for label in self.config.labels.values():
                key = 'nodepool.label.%s.nodes.%s' % (
                    label.name, state)
                states[key] = 0
            for provider in self.config.providers.values():
                key = 'nodepool.provider.%s.nodes.%s' % (
                    provider.name, state)
                states[key] = 0

        managers = set()

        for node in session.getNodes():
            if node.state not in nodedb.STATE_NAMES:
                continue
            state = nodedb.STATE_NAMES[node.state]
            key = 'nodepool.nodes.%s' % state
            states[key] += 1

            # NOTE(pabelanger): Check if we assign nodes via Gearman if so, use
            # the manager name.
            #nodepool.manager.MANAGER.nodes.STATE
            if node.manager_name:
                key = 'nodepool.manager.%s.nodes.%s' % (
                    node.manager_name, state)
                if key not in states:
                    states[key] = 0
                managers.add(node.manager_name)
            else:
                key = 'nodepool.target.%s.nodes.%s' % (
                    node.target_name, state)
            states[key] += 1

            key = 'nodepool.label.%s.nodes.%s' % (
                node.label_name, state)
            states[key] += 1

            key = 'nodepool.provider.%s.nodes.%s' % (
                node.provider_name, state)
            states[key] += 1

        # NOTE(pabelanger): Initialize other state values to zero if missed
        # above.
        #nodepool.manager.MANAGER.nodes.STATE
        for state in nodedb.STATE_NAMES.values():
            for manager_name in managers:
                key = 'nodepool.manager.%s.nodes.%s' % (
                    manager_name, state)
                if key not in states:
                    states[key] = 0

        for key, count in states.items():
            self.statsd.gauge(key, count)

        #nodepool.provider.PROVIDER.max_servers
        for provider in self.config.providers.values():
            key = 'nodepool.provider.%s.max_servers' % provider.name
            self.statsd.gauge(key, provider.max_servers)

    def launchStats(self, subkey, dt, image_name,
                    provider_name, target_name, node_az, manager_name):
        if not self.statsd:
            return
        #nodepool.launch.provider.PROVIDER.subkey
        #nodepool.launch.image.IMAGE.subkey
        #nodepool.launch.subkey
        keys = [
            'nodepool.launch.provider.%s.%s' % (provider_name, subkey),
            'nodepool.launch.image.%s.%s' % (image_name, subkey),
            'nodepool.launch.%s' % (subkey,),
            ]
        if node_az:
            #nodepool.launch.provider.PROVIDER.AZ.subkey
            keys.append('nodepool.launch.provider.%s.%s.%s' %
                        (provider_name, node_az, subkey))

        if manager_name:
            # NOTE(pabelanger): Check if we assign nodes via Gearman if so, use
            # the manager name.
            #nodepool.launch.manager.MANAGER.subkey
            keys.append('nodepool.launch.manager.%s.%s' %
                        (manager_name, subkey))
        else:
            #nodepool.launch.target.TARGET.subkey
            keys.append('nodepool.launch.target.%s.%s' %
                        (target_name, subkey))

        for key in keys:
            self.statsd.timing(key, dt)
            self.statsd.incr(key)

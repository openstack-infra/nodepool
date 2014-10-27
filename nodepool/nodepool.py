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

from statsd import statsd
import apscheduler.scheduler
import gear
import json
import logging
import os.path
import paramiko
import Queue
import random
import re
import shlex
import subprocess
import threading
import time
import yaml
import zmq

import allocation
import jenkins_manager
import nodedb
import nodeutils as utils
import provider_manager

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

        if statsd and self.result == 'SUCCESS':
            start = node.state_time
            end = time.time()
            dt = end - start

            # nodepool.job.tempest
            key = 'nodepool.job.%s' % self.jobname
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

            # nodepool.job.tempest.master
            key += '.%s' % self.branch
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

            # nodepool.job.tempest.master.devstack-precise
            key += '.%s' % node.label_name
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

            # nodepool.job.tempest.master.devstack-precise.rax-ord
            key += '.%s' % node.provider_name
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

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


class GearmanClient(gear.Client):
    def __init__(self):
        super(GearmanClient, self).__init__()
        self.__log = logging.getLogger("nodepool.GearmanClient")

    def getNeededWorkers(self):
        needed_workers = {}
        job_worker_map = {}
        unspecified_jobs = {}
        for connection in self.active_connections:
            try:
                req = gear.StatusAdminRequest()
                connection.sendAdminRequest(req)
            except Exception:
                self.__log.exception("Exception while listing functions")
                continue
            for line in req.response.split('\n'):
                parts = [x.strip() for x in line.split()]
                if not parts or parts[0] == '.':
                    continue
                if not parts[0].startswith('build:'):
                    continue
                function = parts[0][len('build:'):]
                # total jobs in queue - running
                queued = int(parts[1]) - int(parts[2])
                if queued > 0:
                    self.__log.debug("Function: %s queued: %s" % (function,
                                                                  queued))
                if ':' in function:
                    fparts = function.split(':')
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
                                          self.node.az)
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
        server_id = self.manager.createServer(
            hostname, self.image.min_ram, snap_image.external_id,
            name_filter=self.image.name_filter, az=self.node.az)
        self.node.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server_id, self.node.id))
        server = self.manager.waitForServer(server_id, self.launch_timeout)
        if server['status'] != 'ACTIVE':
            raise LaunchStatusException("Server %s for node id: %s "
                                        "status: %s" %
                                        (server_id, self.node.id,
                                         server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server_id,
                                          pool=self.provider.pool)
        if not ip:
            raise LaunchNetworkException("Unable to find public IP of server")

        self.node.ip = ip
        self.log.debug("Node id: %s is running, ip: %s, testing ssh" %
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

            # TODO(jeblair): Remove 'try' once we have updated images
            # everywhere; this is just to help with the transition
            # from images without /etc/nodepool to ones with.
            try:
                host.ssh("test for config dir", "ls /etc/nodepool")

                ftp = host.client.open_sftp()

                f = ftp.open('/etc/nodepool/role', 'w')
                f.write(role + '\n')
                f.close()
                f = ftp.open('/etc/nodepool/primary_node', 'w')
                f.write(self.node.ip + '\n')
                f.close()
                f = ftp.open('/etc/nodepool/sub_nodes', 'w')
                for subnode in self.node.subnodes:
                    f.write(subnode.ip + '\n')
                f.close()
                f = ftp.open('/etc/nodepool/id_rsa', 'w')
                key.write_private_key(f)
                f.close()
                f = ftp.open('/etc/nodepool/id_rsa.pub', 'w')
                f.write(public_key)
                f.close()
                f = ftp.open('/etc/nodepool/provider', 'w')
                f.write('NODEPOOL_PROVIDER=%s\n' % self.provider.name)
                f.write('NODEPOOL_REGION=%s\n' % (
                    self.provider.region_name or '',))
                f.write('NODEPOOL_AZ=%s\n' % (self.node.az or '',))
                f.close()

                ftp.close()
            except Exception:
                self.log.info("/etc/nodepool does not exist on node id: %s"
                              % self.node.id)

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
                 node_id, node_target_name, timeout, launch_timeout, node_az):
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
                                          self.node_az)
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
        server_id = self.manager.createServer(
            hostname, self.image.min_ram, snap_image.external_id,
            name_filter=self.image.name_filter, az=self.node_az)
        self.subnode.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for subnode id: %s for "
                       "node id: %s" %
                       (server_id, self.subnode_id, self.node_id))
        server = self.manager.waitForServer(server_id, self.launch_timeout)
        if server['status'] != 'ACTIVE':
            raise LaunchStatusException("Server %s for subnode id: "
                                        "%s for node id: %s "
                                        "status: %s" %
                                        (server_id, self.subnode_id,
                                         self.node_id, server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server_id,
                                          pool=self.provider.pool)
        if not ip:
            raise LaunchNetworkException("Unable to find public IP of server")

        self.subnode.ip = ip
        self.log.debug("Subnode id: %s for node id: %s is running, "
                       "ip: %s, testing ssh" %
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


class DiskImageBuilder(threading.Thread):
    log = logging.getLogger("nodepool.DiskImageBuilderThread")

    def __init__(self, nodepool):
        threading.Thread.__init__(self, name='DiskImageBuilder queue')
        self.nodepool = nodepool
        self.queue = nodepool._image_builder_queue

    def run(self):
        while True:
            # grabs image id from queue
            self.disk_image = self.queue.get()
            self.buildImage(self.disk_image)
            self.queue.task_done()

    def _buildImage(self, image, image_name, filename):
        try:
            env = os.environ.copy()

            env['DIB_RELEASE'] = image.release
            env['DIB_IMAGE_NAME'] = image_name
            env['DIB_IMAGE_FILENAME'] = filename
            # Note we use a reference to the nodepool config here so
            # that whenever the config is updated we get up to date
            # values in this thread.
            env['ELEMENTS_PATH'] = self.nodepool.config.elementsdir
            env['NODEPOOL_SCRIPTDIR'] = self.nodepool.config.scriptdir

            # send additional env vars if needed
            for k, v in image.env_vars.items():
                env[k] = v

            out_file_path = os.path.join(self.nodepool.config.imagesdir,
                                         filename)

            extra_options = ''
            if image.qemu_img_options:
                extra_options = ('--qemu-img-options %s' %
                                 image.qemu_img_options)
            img_elements = image.elements

            cmd = ('disk-image-create -x --no-tmpfs %s -o %s %s' %
                   (extra_options, out_file_path, img_elements))

            if 'fake-dib-image' in filename:
                cmd = 'echo ' + cmd

            log = logging.getLogger("nodepool.image.build.%s" %
                                    (image_name,))

            self.log.info('Running %s' % cmd)

            p = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env)

            while True:
                ln = p.stdout.readline()
                log.info(ln.strip())
                if not ln:
                    break

            p.wait()
            ret = p.returncode
            if ret:
                raise Exception("Unable to create %s" % filename)
        except Exception:
            self.log.exception("Exception in run method:")

    def buildImage(self, image_id):
        with self.nodepool.getDB().getSession() as session:
            self.dib_image = session.getDibImage(image_id)
            self.log.info("Creating image: %s with filename %s" %
                          (self.dib_image.image_name, self.dib_image.filename))

            start_time = time.time()
            timestamp = int(start_time)
            self.dib_image.version = timestamp
            session.commit()

            # retrieve image details
            image_details = \
                self.nodepool.config.diskimages[self.dib_image.image_name]
            self._buildImage(
                image_details,
                self.dib_image.image_name,
                self.dib_image.filename)

            if statsd:
                dt = int((time.time() - start_time) * 1000)
                key = 'nodepool.dib_image_build.%s' % self.dib_image.image_name
                statsd.timing(key, dt)
                statsd.incr(key)

            self.dib_image.state = nodedb.READY
            session.commit()
            self.log.info("Image %s is built" % self.dib_image.image_name)


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


class DiskImageUpdater(ImageUpdater):

    log = logging.getLogger("nodepool.DiskImageUpdater")

    def __init__(self, nodepool, provider, image, snap_image_id,
                 filename):
        super(DiskImageUpdater, self).__init__(nodepool, provider, image,
                                               snap_image_id)
        self.filename = filename
        self.image_name = image.name

    def updateImage(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        dummy_image = type('obj', (object,), {'name': self.image_name})
        image_name = self.provider.template_hostname.format(
            provider=self.provider, image=dummy_image,
            timestamp=str(timestamp))

        # TODO(mordred) abusing the hostname field
        self.snap_image.hostname = image_name
        self.snap_image.version = timestamp
        session.commit()

        # strip extension from filename
        stripped_filename = self.filename.replace(".qcow2", "")
        image_path = os.path.join(self.imagesdir, stripped_filename)
        image_id = self.manager.uploadImage(image_name, image_path,
                                            'qcow2', 'bare', self.image.meta)
        self.snap_image.external_id = image_id
        session.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image_id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        self.manager.waitForImage(image_id, IMAGE_TIMEOUT)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (self.image_name,
                                                   self.provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        self.snap_image.state = nodedb.READY
        session.commit()
        self.log.info("Image %s in %s is ready" % (self.snap_image.hostname,
                                                   self.provider.name))


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
        elif self.manager.hasExtension('os-keypairs'):
            key_name = hostname.split('.')[0]
            key = self.manager.addKeypair(key_name)
            use_password = False
        else:
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
            server_id = self.manager.createServer(
                hostname, self.image.min_ram, image_name=image_name,
                key_name=key_name, name_filter=self.image.name_filter,
                image_id=image_id)
        except Exception:
            if (self.manager.hasExtension('os-keypairs') and
                not self.provider.keypair):
                for kp in self.manager.listKeypairs():
                    if kp['name'] == key_name:
                        self.log.debug(
                            'Deleting keypair for failed image build %s' %
                            self.snap_image.id)
                        self.manager.deleteKeypair(kp['name'])
            raise

        self.snap_image.hostname = hostname
        self.snap_image.version = timestamp
        self.snap_image.server_external_id = server_id
        session.commit()

        self.log.debug("Image id: %s waiting for server %s" %
                       (self.snap_image.id, server_id))
        server = self.manager.waitForServer(server_id)
        if server['status'] != 'ACTIVE':
            raise Exception("Server %s for image id: %s status: %s" %
                            (server_id, self.snap_image.id, server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server_id,
                                          pool=self.provider.pool)
        if not ip:
            raise Exception("Unable to find public IP of server")
        server['public_v4'] = ip

        self.bootstrapServer(server, key, use_password=use_password)

        image_id = self.manager.createImage(server_id, hostname,
                                            self.image.meta)
        self.snap_image.external_id = image_id
        session.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image_id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        image = self.manager.waitForImage(image_id, IMAGE_TIMEOUT)
        if image['status'] != 'ACTIVE':
            raise Exception("Image %s for image id: %s status: %s" %
                            (image_id, self.snap_image.id, image['status']))

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (self.image.name,
                                                   self.provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

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

        host = utils.ssh_connect(server['public_v4'], 'root', ssh_kwargs,
                                 timeout=CONNECT_TIMEOUT)

        if not host:
            # We have connected to the node but couldn't do anything as root
            # try distro specific users, since we know ssh is up (a timeout
            # didn't occur), we can connect with a very sort timeout.
            for username in ['ubuntu', 'fedora', 'cloud-user', 'centos']:
                try:
                    host = utils.ssh_connect(server['public_v4'], username,
                                             ssh_kwargs,
                                             timeout=10)
                    if host:
                        break
                except:
                    continue

        if not host:
            raise Exception("Unable to log in via SSH")

        host.ssh("make scripts dir", "mkdir -p scripts")
        # /etc/nodepool is world writable because by the time we write
        # the contents after the node is launched, we may not have
        # sudo access any more.
        host.ssh("make config dir", "sudo mkdir /etc/nodepool")
        host.ssh("chmod config dir", "sudo chmod 0777 /etc/nodepool")
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
            host.ssh("run setup script",
                     "cd /opt/nodepool-scripts && %s ./%s %s" %
                     (env_vars, self.image.setup, server['name']))


class ConfigValue(object):
    pass


class Config(ConfigValue):
    pass


class Provider(ConfigValue):
    pass


class ProviderImage(ConfigValue):
    pass


class Target(ConfigValue):
    pass


class Label(ConfigValue):
    pass


class LabelProvider(ConfigValue):
    pass


class Cron(ConfigValue):
    pass


class ZMQPublisher(ConfigValue):
    pass


class GearmanServer(ConfigValue):
    pass


class DiskImage(ConfigValue):
    pass


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    allocation_history = allocation.AllocationHistory()

    def __init__(self, configfile, watermark_sleep=WATERMARK_SLEEP):
        threading.Thread.__init__(self, name='NodePool')
        self.configfile = configfile
        self.watermark_sleep = watermark_sleep
        self._stopped = False
        self.config = None
        self.zmq_context = None
        self.gearman_client = None
        self.apsched = None
        self._delete_threads = {}
        self._delete_threads_lock = threading.Lock()
        self._image_delete_threads = {}
        self._image_delete_threads_lock = threading.Lock()
        self._image_builder_queue = Queue.Queue()
        self._image_builder_thread = None

    def stop(self):
        self._stopped = True
        if self.config:
            for z in self.config.zmq_publishers.values():
                z.listener.stop()
                z.listener.join()
        if self.zmq_context:
            self.zmq_context.destroy()
        if self.apsched:
            self.apsched.shutdown()

    def waitForBuiltImages(self):
        # wait on the queue until everything has been processed
        self._image_builder_queue.join()

    def loadConfig(self):
        self.log.debug("Loading configuration")
        config = yaml.load(open(self.configfile))

        newconfig = Config()
        newconfig.db = None
        newconfig.dburi = None
        newconfig.providers = {}
        newconfig.targets = {}
        newconfig.labels = {}
        newconfig.scriptdir = config.get('script-dir')
        newconfig.elementsdir = config.get('elements-dir')
        newconfig.imagesdir = config.get('images-dir')
        newconfig.dburi = config.get('dburi')
        newconfig.provider_managers = {}
        newconfig.jenkins_managers = {}
        newconfig.zmq_publishers = {}
        newconfig.gearman_servers = {}
        newconfig.diskimages = {}
        newconfig.crons = {}

        for name, default in [
            ('image-update', '14 2 * * *'),
            ('cleanup', '* * * * *'),
            ('check', '*/15 * * * *'),
            ]:
            c = Cron()
            c.name = name
            newconfig.crons[c.name] = c
            c.job = None
            c.timespec = config.get('cron', {}).get(name, default)

        for addr in config['zmq-publishers']:
            z = ZMQPublisher()
            z.name = addr
            z.listener = None
            newconfig.zmq_publishers[z.name] = z

        for server in config.get('gearman-servers', []):
            g = GearmanServer()
            g.host = server['host']
            g.port = server.get('port', 4730)
            g.name = g.host + '_' + str(g.port)
            newconfig.gearman_servers[g.name] = g

        if 'diskimages' in config:
            for diskimage in config['diskimages']:
                d = DiskImage()
                d.name = diskimage['name']
                newconfig.diskimages[d.name] = d
                if 'elements' in diskimage:
                    d.elements = u' '.join(diskimage['elements'])
                else:
                    d.elements = ''
                d.release = diskimage.get('release', '')
                d.qemu_img_options = diskimage.get('qemu-img-options', '')
                if 'env-vars' in diskimage:
                    d.env_vars = diskimage['env-vars']
                else:
                    d.env_vars = {}

        for label in config['labels']:
            l = Label()
            l.name = label['name']
            newconfig.labels[l.name] = l
            l.image = label['image']
            l.min_ready = label.get('min-ready', 2)
            l.subnodes = label.get('subnodes', 0)
            l.ready_script = label.get('ready-script')
            l.providers = {}
            for provider in label['providers']:
                p = LabelProvider()
                p.name = provider['name']
                l.providers[p.name] = p

            # if image is in diskimages, mark it to build once
            l.is_diskimage = (l.image in newconfig.diskimages)

        for provider in config['providers']:
            p = Provider()
            p.name = provider['name']
            newconfig.providers[p.name] = p
            p.username = provider['username']
            p.password = provider['password']
            p.project_id = provider['project-id']
            p.auth_url = provider['auth-url']
            p.service_type = provider.get('service-type')
            p.service_name = provider.get('service-name')
            p.region_name = provider.get('region-name')
            p.max_servers = provider['max-servers']
            p.keypair = provider.get('keypair', None)
            p.pool = provider.get('pool')
            p.rate = provider.get('rate', 1.0)
            p.boot_timeout = provider.get('boot-timeout', 60)
            p.launch_timeout = provider.get('launch-timeout', 3600)
            p.use_neutron = bool(provider.get('networks', ()))
            p.networks = provider.get('networks')
            p.azs = provider.get('availability-zones')
            p.template_hostname = provider.get(
                'template-hostname',
                '{image.name}-{timestamp}.template.openstack.org'
            )
            p.images = {}
            for image in provider['images']:
                i = ProviderImage()
                i.name = image['name']
                p.images[i.name] = i
                i.base_image = image.get('base-image', None)
                i.min_ram = image['min-ram']
                i.name_filter = image.get('name-filter', None)
                i.setup = image.get('setup', None)
                i.reset = image.get('reset')
                i.diskimage = image.get('diskimage', None)
                i.username = image.get('username', 'jenkins')
                i.user_home = image.get('user-home', '/home/jenkins')
                i.private_key = image.get('private-key',
                                          '/var/lib/jenkins/.ssh/id_rsa')

                # note this does "double-duty" -- for
                # SnapshotImageUpdater the meta-data dict is passed to
                # nova when the snapshot image is created.  For
                # DiskImageUpdater, this dict is expanded and used as
                # custom properties when the image is uploaded.
                i.meta = image.get('meta', {})
                # 5 elements, and no key or value can be > 255 chars
                # per novaclient.servers.create() rules
                if i.meta:
                    if len(i.meta) > 5 or \
                       any([len(k) > 255 or len(v) > 255
                            for k, v in i.meta.iteritems()]):
                        # soft-fail
                        self.log.error("Invalid metadata for %s; ignored"
                                       % i.name)
                        i.meta = {}

        for target in config['targets']:
            t = Target()
            t.name = target['name']
            newconfig.targets[t.name] = t
            jenkins = target.get('jenkins')
            t.online = True
            if jenkins:
                t.jenkins_url = jenkins['url']
                t.jenkins_user = jenkins['user']
                t.jenkins_apikey = jenkins['apikey']
                t.jenkins_credentials_id = jenkins.get('credentials-id')
                t.jenkins_test_job = jenkins.get('test-job')
            else:
                t.jenkins_url = None
                t.jenkins_user = None
                t.jenkins_apikey = None
                t.jenkins_credentials_id = None
                t.jenkins_test_job = None
            t.rate = target.get('rate', 1.0)
            t.hostname = target.get(
                'hostname',
                '{label.name}-{provider.name}-{node_id}.slave.openstack.org'
            )
            t.subnode_hostname = target.get(
                'subnode-hostname',
                '{label.name}-{provider.name}-{node_id}-{subnode_id}'
                '.slave.openstack.org'
            )
        return newconfig

    def reconfigureDatabase(self, config):
        if (not self.config) or config.dburi != self.config.dburi:
            config.db = nodedb.NodeDatabase(config.dburi)
        else:
            config.db = self.config.db

    def _managersEquiv(self, new_pm, old_pm):
        # Check if provider details have changed
        if (new_pm.username != old_pm.provider.username or
            new_pm.password != old_pm.provider.password or
            new_pm.project_id != old_pm.provider.project_id or
            new_pm.auth_url != old_pm.provider.auth_url or
            new_pm.service_type != old_pm.provider.service_type or
            new_pm.service_name != old_pm.provider.service_name or
            new_pm.max_servers != old_pm.provider.max_servers or
            new_pm.pool != old_pm.provider.pool or
            new_pm.rate != old_pm.provider.rate or
            new_pm.boot_timeout != old_pm.provider.boot_timeout or
            new_pm.launch_timeout != old_pm.provider.launch_timeout or
            new_pm.use_neutron != old_pm.provider.use_neutron or
            new_pm.networks != old_pm.provider.networks or
            new_pm.azs != old_pm.provider.azs):
            return False
        new_images = new_pm.images
        old_images = old_pm.provider.images
        # Check if new images have been added
        if set(new_images.keys()) != set(old_images.keys()):
            return False
        # check if existing images have been updated
        for k in new_images:
            if (new_images[k].base_image != old_images[k].base_image or
                new_images[k].min_ram != old_images[k].min_ram or
                new_images[k].name_filter != old_images[k].name_filter or
                new_images[k].setup != old_images[k].setup or
                new_images[k].reset != old_images[k].reset or
                new_images[k].username != old_images[k].username or
                new_images[k].user_home != old_images[k].user_home or
                new_images[k].private_key != old_images[k].private_key or
                new_images[k].meta != old_images[k].meta):
                return False
        return True

    def reconfigureManagers(self, config):
        stop_managers = []
        for p in config.providers.values():
            oldmanager = None
            if self.config:
                oldmanager = self.config.provider_managers.get(p.name)
            if oldmanager and not self._managersEquiv(p, oldmanager):
                stop_managers.append(oldmanager)
                oldmanager = None
            if oldmanager:
                config.provider_managers[p.name] = oldmanager
            else:
                self.log.debug("Creating new ProviderManager object for %s" %
                               p.name)
                config.provider_managers[p.name] = \
                    provider_manager.ProviderManager(p)
                config.provider_managers[p.name].start()

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
            else:
                self.log.debug("Creating new JenkinsManager object for %s" %
                               t.name)
                config.jenkins_managers[t.name] = \
                    jenkins_manager.JenkinsManager(t)
                config.jenkins_managers[t.name].start()
        for oldmanager in stop_managers:
            oldmanager.stop()

        for t in config.targets.values():
            try:
                info = config.jenkins_managers[t.name].getInfo()
                if info['quietingDown']:
                    self.log.info("Target %s is offline" % t.name)
                    t.online = False
                else:
                    t.online = True
            except Exception:
                self.log.exception("Unable to check status of %s" % t.name)
                t.online = False

    def reconfigureCrons(self, config):
        cron_map = {
            'image-update': self._doUpdateImages,
            'cleanup': self._doPeriodicCleanup,
            'check': self._doPeriodicCheck,
            }

        if not self.apsched:
            self.apsched = apscheduler.scheduler.Scheduler()
            self.apsched.start()

        for c in config.crons.values():
            if ((not self.config) or
                c.timespec != self.config.crons[c.name].timespec):
                if self.config and self.config.crons[c.name].job:
                    self.apsched.unschedule_job(self.config.crons[c.name].job)
                parts = c.timespec.split()
                minute, hour, dom, month, dow = parts[:5]
                c.job = self.apsched.add_cron_job(
                    cron_map[c.name],
                    day=dom,
                    day_of_week=dow,
                    hour=hour,
                    minute=minute)
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

    def reconfigureImageBuilder(self):
        # start disk image builder thread
        if not self._image_builder_thread:
            self._image_builder_thread = DiskImageBuilder(self)
            self._image_builder_thread.daemon = True
            self._image_builder_thread.start()

    def setConfig(self, config):
        self.config = config

    def getDB(self):
        return self.config.db

    def getProviderManager(self, provider):
        return self.config.provider_managers[provider.name]

    def getJenkinsManager(self, target):
        return self.config.jenkins_managers[target.name]

    def getNeededNodes(self, session):
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
        # Actual need is demand - (ready + building)
        for label in self.config.labels.values():
            start_demand = label_demand.get(label.name, 0)

            # ignore min-ready if it doesn't look like we'll have the
            # capacity to deal with it.
            capacity = 0
            for provider in label.providers.values():
                capacity += allocation_providers[provider.name].available
            if capacity < label.min_ready:
                min_demand = start_demand
            else:
                min_demand = start_demand + label.min_ready

            n_ready = count_nodes(label.name, nodedb.READY)
            n_building = count_nodes(label.name, nodedb.BUILDING)
            n_test = count_nodes(label.name, nodedb.TEST)
            ready = n_ready + n_building + n_test
            demand = max(min_demand - ready, 0)
            label_demand[label.name] = demand
            self.log.debug("  Deficit: %s: %s "
                           "(start: %s min: %s ready: %s capacity: %s)" %
                           (label.name, demand,
                            start_demand, min_demand, ready, capacity))

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
                                                      self.allocation_history)

                nodes = session.getNodes(label_name=label.name,
                                         target_name=target.name)
                allocation_requests[label.name] = ar
                ar.addTarget(at, len(nodes))
                for provider in label.providers.values():
                    # This request may be supplied by this provider
                    # (and nodes from this provider supplying this
                    # request should be distributed to this target).
                    sr, agt = ar.addProvider(
                        allocation_providers[provider.name],
                        at, label.subnodes)
                    tlps[agt] = (target, label,
                                 self.config.providers[provider.name])

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

        self.allocation_history.grantsDone()

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
        self.reconfigureCrons(config)
        self.reconfigureUpdateListeners(config)
        self.reconfigureGearmanClient(config)
        self.setConfig(config)
        self.reconfigureImageBuilder()

    def startup(self):
        self.updateConfig()

        # Currently nodepool can not resume building a node after a
        # restart.  To clean up, mark all building nodes for deletion
        # when the daemon starts.
        with self.getDB().getSession() as session:
            for node in session.getNodes(state=nodedb.BUILDING):
                self.log.info("Setting building node id: %s to delete "
                              "on startup" % node.id)
                node.state = nodedb.DELETE

    def run(self):
        try:
            self.startup()
        except Exception:
            self.log.exception("Exception in startup:")
        while not self._stopped:
            try:
                self.updateConfig()
                with self.getDB().getSession() as session:
                    self._run(session)
            except Exception:
                self.log.exception("Exception in main loop:")
            time.sleep(self.watermark_sleep)

    def _run(self, session):
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

        nodes_to_launch = self.getNeededNodes(session)

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

    def checkForMissingImages(self, session):
        # If we are missing an image, run the image update function
        # outside of its schedule.
        self.log.debug("Checking missing images.")
        for label in self.config.labels.values():
            if label.min_ready < 0:
                # Label is configured to be disabled, skip creating the image.
                continue

            # check if image is there, if not, build it
            if label.is_diskimage:
                found = False
                for dib_image in session.getDibImages():
                    if (dib_image.image_name == label.image and
                            dib_image.state in [nodedb.READY,
                                                nodedb.BUILDING]):
                        # if image is in ready state, check if image
                        # file exists in directory, otherwise we need
                        # to rebuild and delete this buggy image
                        if (dib_image.state == nodedb.READY and
                                not os.path.exists(dib_image.filename) and
                                not 'fake-dib-image' in dib_image.filename):
                            self.log.warning("Image filename %s does not "
                                             "exist. Removing image" %
                                             dib_image.filename)
                            self.deleteDibImage(dib_image)
                            continue

                        found = True
                        break
                if not found:
                    # only build the image, we'll recheck again
                    self.log.warning("Missing disk image %s" % label.image)
                    self.buildImage(self.config.diskimages[label.image])
                else:
                    # check for providers, to upload it
                    for provider in label.providers.values():
                        found = False
                        for snap_image in session.getSnapshotImages():
                            if (snap_image.provider_name == provider.name and
                                snap_image.image_name == label.image and
                                snap_image.state in [nodedb.READY,
                                                     nodedb.BUILDING]):
                                found = True
                                break
                        if not found:
                            self.log.warning("Missing image %s on %s" %
                                             (label.image, provider.name))
                            # when we have a READY image, upload it
                            available_images = \
                                session.getOrderedReadyDibImages(label.image)
                            if available_images:
                                self.uploadImage(session, provider.name,
                                                 label.image)
            else:
                # snapshots
                for provider in label.providers.values():
                    found = False
                    for snap_image in session.getSnapshotImages():
                        if (snap_image.provider_name == provider.name and
                            snap_image.image_name == label.image and
                            snap_image.state in [nodedb.READY,
                                                 nodedb.BUILDING]):
                            found = True
                    if not found:
                        self.log.warning("Missing image %s on %s" %
                                         (label.image, provider.name))
                        self.updateImage(session, provider.name, label.image)

    def _doUpdateImages(self):
        try:
            with self.getDB().getSession() as session:
                self.updateImages(session)
        except Exception:
            self.log.exception("Exception in periodic image update:")

    def updateImages(self, session):
        # This function should be run periodically to create new snapshot
        # images.
        needs_build = False
        for label in self.config.labels.values():
            if label.min_ready < 0:
                # Label is configured to be disabled, skip creating the image.
                continue

            # check if image is there, if not, build it
            if label.is_diskimage:
                self.buildImage(self.config.diskimages[label.image])
                needs_build = True
        if needs_build:
            # wait for all builds to finish, to have updated images to upload
            self.waitForBuiltImages()

        for label in self.config.labels.values():
            if label.min_ready < 0:
                # Label is configured to be disabled, skip creating the image.
                continue

            for provider in label.providers.values():
                if label.is_diskimage:
                    self.uploadImage(session, provider.name, label.image)
                else:
                    self.updateImage(session, provider.name, label.image)

    def updateImage(self, session, provider_name, image_name):
        try:
            self._updateImage(session, provider_name, image_name)
        except Exception:
            self.log.exception(
                "Could not update image %s on %s", image_name, provider_name)

    def _updateImage(self, session, provider_name, image_name):
        # check type of image depending on label
        is_diskimage = (image_name in self.config.diskimages)
        if is_diskimage:
            raise Exception(
                "Cannot update disk image images. "
                "Please build and upload images")

        provider = self.config.providers[provider_name]
        image = provider.images[image_name]
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
        # check type of image depending on label
        is_diskimage = (image.name in self.config.diskimages)
        if not is_diskimage:
            raise Exception(
                "Cannot build non disk image images. "
                "Please create snapshots for them")

        # check if we already have this item in the queue
        with self.getDB().getSession() as session:
            queued_images = session.getBuildingDibImagesByName(image.name)
            if queued_images:
                self.log.exception('Image %s is already being built' %
                                   image.name)
            else:
                try:
                    start_time = time.time()
                    timestamp = int(start_time)

                    filename = os.path.join(self.config.imagesdir,
                                            '%s-%s' %
                                            (image.name, str(timestamp)))

                    self.log.debug("Queued image building task for %s" %
                                   image.name)
                    dib_image = session.createDibImage(image_name=image.name,
                                                       filename=filename +
                                                       ".qcow2")

                    # add this build to queue
                    self._image_builder_queue.put(dib_image.id)
                except Exception:
                    self.log.exception(
                        "Could not build image %s", image.name)

    def uploadImage(self, session, provider, image_name):
        images = session.getOrderedReadyDibImages(image_name)
        if not images:
            # raise error, no image ready for uploading
            raise Exception(
                "No image available for that upload. Please build one first.")

        try:
            filename = images[0].filename
            provider_entity = self.config.providers[provider]
            provider_image = provider_entity.images[images[0].image_name]
            snap_image = session.createSnapshotImage(
                provider_name=provider, image_name=image_name)
            t = DiskImageUpdater(self, provider_entity, provider_image,
                                 snap_image.id, filename)
            t.start()

            # Enough time to give them different timestamps (versions)
            # Just to keep things clearer.
            time.sleep(2)
            return t
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
                            node_az=node.az)
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
        if label:
            image_name = provider.images[label.image].name
        else:
            image_name = None
        manager = self.getProviderManager(provider)

        if target.jenkins_url:
            jenkins = self.getJenkinsManager(target)
            jenkins_name = node.nodename
            if jenkins.nodeExists(jenkins_name):
                jenkins.deleteNode(jenkins_name)
            self.log.info("Deleted jenkins node id: %s" % node.id)

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

        if statsd:
            dt = int((time.time() - node.state_time) * 1000)
            key = 'nodepool.delete.%s.%s.%s' % (image_name,
                                                node.provider_name,
                                                node.target_name)
            statsd.timing(key, dt)
            statsd.incr(key)
        self.updateStats(session, node.provider_name)

    def deleteImage(self, snap_image_id):
        try:
            self._image_delete_threads_lock.acquire()
            if snap_image_id in self._image_delete_threads:
                return
            t = ImageDeleter(self, snap_image_id)
            self._image_delete_threads[snap_image_id] = t
            t.start()
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
                self.log.debug('Deleting server %s for image id: %s' %
                               (snap_image.server_external_id,
                                snap_image.id))
                manager.cleanupServer(server['id'])
                manager.waitForServerDeletion(server['id'])
            except provider_manager.NotFound:
                self.log.warning('Image server id %s not found' %
                                 snap_image.server_external_id)

        if snap_image.external_id:
            try:
                remote_image = manager.getImage(snap_image.external_id)
                self.log.debug('Deleting image %s' % remote_image['id'])
                manager.deleteImage(remote_image['id'])
            except provider_manager.NotFound:
                self.log.warning('Image id %s not found' %
                                 snap_image.external_id)

        snap_image.delete()
        self.log.info("Deleted image id: %s" % snap_image.id)

    def deleteDibImage(self, dib_image):
        # Delete a dib image and it's associated file
        if os.path.exists(dib_image.filename):
            os.remove(dib_image.filename)

        dib_image.state = nodedb.DELETE
        dib_image.delete()
        self.log.info("Deleted dib image id: %s" % dib_image.id)

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
                    self.cleanupOneDibImage(session, dib_image)
            except Exception:
                self.log.exception("Exception cleaning up image id %s:" %
                                   dib_image_id)
        self.log.debug("Finished periodic cleanup")

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
            self.log.exception("Exception in periodic chack:")

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
        if not statsd:
            return
        # This may be called outside of the main thread.
        provider = self.config.providers[provider_name]

        states = {}

        #nodepool.target.TARGET.LABEL.min_ready
        #nodepool.target.TARGET.LABEL.PROVIDER.STATE
        #nodepool.target.STATE
        base_key = 'nodepool.target'
        for target in self.config.targets.values():
            target_key = '%s.%s' % (base_key, target.name)
            for label in self.config.labels.values():
                label_key = '%s.%s' % (target_key, label.name)
                key = '%s.min_ready' % label_key
                statsd.gauge(key, label.min_ready)
                for provider in label.providers.values():
                    provider_key = '%s.%s' % (label_key, provider.name)
                    for state in nodedb.STATE_NAMES.values():
                        base_state_key = '%s.%s' % (base_key, state)
                        provider_state_key = '%s.%s' % (provider_key, state)
                        for key in [base_state_key, provider_state_key]:
                            states[key] = 0

        for node in session.getNodes():
            if node.state not in nodedb.STATE_NAMES:
                continue
            state = nodedb.STATE_NAMES[node.state]
            target_key = '%s.%s' % (base_key, node.target_name)
            label_key = '%s.%s' % (target_key, node.label_name)
            provider_key = '%s.%s' % (label_key, node.provider_name)

            base_state_key = '%s.%s' % (base_key, state)
            provider_state_key = '%s.%s' % (provider_key, state)

            for key in [base_state_key, provider_state_key]:
                if key not in states:
                    states[key] = 0
                states[key] += 1

        for key, count in states.items():
            statsd.gauge(key, count)

        #nodepool.provider.PROVIDER.max_servers
        for provider in self.config.providers.values():
            key = 'nodepool.provider.%s.max_servers' % provider.name
            statsd.gauge(key, provider.max_servers)

    def launchStats(self, subkey, dt, image_name,
                    provider_name, target_name, node_az):
        if not statsd:
            return
        #nodepool.launch.provider.PROVIDER.subkey
        #nodepool.launch.image.IMAGE.subkey
        #nodepool.launch.target.TARGET.subkey
        #nodepool.launch.subkey
        keys = [
            'nodepool.launch.provider.%s.%s' % (provider_name, subkey),
            'nodepool.launch.image.%s.%s' % (image_name, subkey),
            'nodepool.launch.target.%s.%s' % (target_name, subkey),
            'nodepool.launch.%s' % (subkey,),
            ]
        if node_az:
            #nodepool.launch.provider.PROVIDER.AZ.subkey
            keys.append('nodepool.launch.provider.%s.%s.%s' %
                        (provider_name, node_az, subkey))
        for key in keys:
            statsd.timing(key, dt)
            statsd.incr(key)

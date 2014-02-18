#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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

WATERMARK_SLEEP = 5          # Interval between checking if new servers needed
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
CONNECT_TIMEOUT = 10 * MINS  # How long to try to connect after a server
                             # is ACTIVE
NODE_CLEANUP = 8 * HOURS     # When to start deleting a node that is not
                             # READY or HOLD
TEST_CLEANUP = 5 * MINS      # When to start deleting a node that is in TEST
KEEP_OLD_IMAGE = 24 * HOURS  # How long to keep an old (good) image
DELETE_DELAY = 1 * MINS      # Delay before deleting a node that has completed
                             # its job.


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
            key += '.%s' % node.image_name
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

            # nodepool.job.tempest.master.devstack-precise.rax-ord
            key += '.%s' % node.provider_name
            statsd.timing(key + '.runtime', dt)
            statsd.incr(key + '.builds')

        time.sleep(DELETE_DELAY)
        self.nodepool.deleteNode(session, node)


class NodeUpdateListener(threading.Thread):
    log = logging.getLogger("nodepool.NodeUpdateListener")

    def __init__(self, nodepool, addr):
        threading.Thread.__init__(self, name='NodeUpdateListener')
        self.nodepool = nodepool
        self.socket = self.nodepool.zmq_context.socket(zmq.SUB)
        event_filter = b""
        self.socket.setsockopt(zmq.SUBSCRIBE, event_filter)
        self.socket.connect(addr)
        self._stopped = False

    def run(self):
        while not self._stopped:
            m = self.socket.recv().decode('utf-8')
            try:
                topic, data = m.split(None, 1)
                self.handleEvent(topic, data)
            except Exception:
                self.log.exception("Exception handling job:")

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


class NodeLauncher(threading.Thread):
    log = logging.getLogger("nodepool.NodeLauncher")

    def __init__(self, nodepool, provider, image, target, node_id, timeout):
        threading.Thread.__init__(self, name='NodeLauncher for %s' % node_id)
        self.provider = provider
        self.image = image
        self.target = target
        self.node_id = node_id
        self.timeout = timeout
        self.nodepool = nodepool

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
                self.launchNode(session)
            except Exception:
                self.log.exception("Exception launching node id: %s:" %
                                   self.node_id)
                try:
                    self.nodepool.deleteNode(session, self.node)
                except Exception:
                    self.log.exception("Exception deleting node id: %s:" %
                                       self.node_id)
                    return

    def launchNode(self, session):
        start_time = time.time()

        hostname = '%s-%s-%s.slave.openstack.org' % (
            self.image.name, self.provider.name, self.node.id)
        self.node.hostname = hostname
        self.node.nodename = hostname.split('.')[0]
        self.node.target_name = self.target.name

        snap_image = session.getCurrentSnapshotImage(
            self.provider.name, self.image.name)
        if not snap_image:
            raise Exception("Unable to find current snapshot image %s in %s" %
                            (self.image.name, self.provider.name))

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self.provider.name,
                                           self.image.name, self.node_id))
        server_id = self.manager.createServer(
            hostname, self.image.min_ram,
            snap_image.external_id, name_filter=self.image.name_filter)
        self.node.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server_id, self.node.id))
        server = self.manager.waitForServer(server_id)
        if server['status'] != 'ACTIVE':
            raise Exception("Server %s for node id: %s status: %s" %
                            (server_id, self.node.id, server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server_id,
                                          pool=self.provider.pool)
        if not ip:
            raise Exception("Unable to find public IP of server")

        self.node.ip = ip
        self.log.debug("Node id: %s is running, ip: %s, testing ssh" %
                       (self.node.id, ip))
        connect_kwargs = dict(key_filename=self.image.private_key)
        if not utils.ssh_connect(ip, self.image.username,
                                 connect_kwargs=connect_kwargs,
                                 timeout=self.timeout):
            raise Exception("Unable to connect via ssh")

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.launch.%s.%s.%s' % (self.image.name,
                                                self.provider.name,
                                                self.target.name)
            statsd.timing(key, dt)
            statsd.incr(key)

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

    def createJenkinsNode(self):
        jenkins = self.nodepool.getJenkinsManager(self.target)

        args = dict(name=self.node.nodename,
                    host=self.node.ip,
                    description='Dynamic single use %s node' % self.image.name,
                    executors=1,
                    root='/home/jenkins')
        if not self.target.jenkins_test_job:
            args['labels'] = self.image.name
        if self.target.jenkins_credentials_id:
            args['credentials_id'] = self.target.jenkins_credentials_id
        else:
            args['username'] = self.image.username
            args['private_key'] = self.image.private_key

        jenkins.createNode(**args)

        if self.target.jenkins_test_job:
            params = dict(NODE=self.node.nodename)
            jenkins.startBuild(self.target.jenkins_test_job, params)


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
                        self.nodepool.deleteImage(self.snap_image)
                except Exception:
                    self.log.exception("Exception deleting image id: %s:" %
                                       self.snap_image.id)
                    return

    def updateImage(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        hostname = ('%s-%s.template.openstack.org' %
                    (self.image.name, str(timestamp)))
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

        server_id = self.manager.createServer(
            hostname, self.image.min_ram, image_name=self.image.base_image,
            key_name=key_name, name_filter=self.image.name_filter)
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

        image_id = self.manager.createImage(server_id, hostname)
        self.snap_image.external_id = image_id
        session.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image_id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        self.manager.waitForImage(image_id, IMAGE_TIMEOUT)

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

        for username in ['root', 'ubuntu']:
            host = utils.ssh_connect(server['public_v4'], username,
                                     ssh_kwargs,
                                     timeout=CONNECT_TIMEOUT)
            if host:
                break

        if not host:
            raise Exception("Unable to log in via SSH")

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
            host.ssh("run setup script",
                     "cd /opt/nodepool-scripts && %s ./%s" %
                     (env_vars, self.image.setup))


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


class TargetImage(ConfigValue):
    pass


class TargetImageProvider(ConfigValue):
    pass


class Cron(ConfigValue):
    pass


class ZMQPublisher(ConfigValue):
    pass


class GearmanServer(ConfigValue):
    pass


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, configfile):
        threading.Thread.__init__(self, name='NodePool')
        self.configfile = configfile
        self._stopped = False
        self.config = None
        self.zmq_context = None
        self.gearman_client = None
        self.apsched = None

    def stop(self):
        self._stopped = True
        if self.zmq_context:
            self.zmq_context.destroy()
        if self.apsched:
            self.apsched.shutdown()

    def loadConfig(self):
        self.log.debug("Loading configuration")
        config = yaml.load(open(self.configfile))

        newconfig = Config()
        newconfig.db = None
        newconfig.dburi = None
        newconfig.providers = {}
        newconfig.targets = {}
        newconfig.scriptdir = config.get('script-dir')
        newconfig.dburi = config.get('dburi')
        newconfig.provider_managers = {}
        newconfig.jenkins_managers = {}
        newconfig.zmq_publishers = {}
        newconfig.gearman_servers = {}
        newconfig.crons = {}

        for name, default in [
            ('image-update', '14 2 * * *'),
            ('cleanup', '27 */6 * * *'),
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
            p.use_neutron = bool(provider.get('networks', ()))
            if p.use_neutron:
                p.nics = provider.get('networks')
            p.images = {}
            for image in provider['images']:
                i = ProviderImage()
                i.name = image['name']
                p.images[i.name] = i
                i.base_image = image['base-image']
                i.min_ram = image['min-ram']
                i.name_filter = image.get('name-filter', None)
                i.setup = image.get('setup')
                i.reset = image.get('reset')
                i.username = image.get('username', 'jenkins')
                i.private_key = image.get('private-key',
                                          '/var/lib/jenkins/.ssh/id_rsa')

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
            t.images = {}
            for image in target['images']:
                i = TargetImage()
                i.name = image['name']
                t.images[i.name] = i
                i.providers = {}
                i.min_ready = image['min-ready']
                for provider in image['providers']:
                    p = TargetImageProvider()
                    p.name = provider['name']
                    i.providers[p.name] = p

        return newconfig

    def reconfigureDatabase(self, config):
        if (not self.config) or config.dburi != self.config.dburi:
            config.db = nodedb.NodeDatabase(config.dburi)
        else:
            config.db = self.config.db

    def reconfigureManagers(self, config):
        stop_managers = []
        for p in config.providers.values():
            oldmanager = None
            if self.config:
                oldmanager = self.config.provider_managers.get(p.name)
            if oldmanager:
                if (p.username != oldmanager.provider.username or
                    p.password != oldmanager.provider.password or
                    p.project_id != oldmanager.provider.project_id or
                    p.auth_url != oldmanager.provider.auth_url or
                    p.service_type != oldmanager.provider.service_type or
                    p.service_name != oldmanager.provider.service_name or
                    p.max_servers != oldmanager.provider.max_servers or
                    p.pool != oldmanager.provider.pool or
                    p.rate != oldmanager.provider.rate or
                    p.boot_timeout != oldmanager.provider.boot_timeout):
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
            image_demand = self.gearman_client.getNeededWorkers()
        else:
            image_demand = {}

        for name, demand in image_demand.items():
            self.log.debug("  Demand from gearman: %s: %s" % (name, demand))

        # Make sure that the current demand includes at least the
        # configured min_ready values
        total_image_min_ready = {}
        online_targets = set()
        for target in self.config.targets.values():
            if not target.online:
                continue
            online_targets.add(target.name)
            for image in target.images.values():
                min_ready = total_image_min_ready.get(image.name, 0)
                min_ready += image.min_ready
                total_image_min_ready[image.name] = min_ready

        def count_nodes(image_name, state):
            nodes = session.getNodes(image_name=image_name,
                                     state=state)
            return len([n for n in nodes
                        if n.target_name in online_targets])

        # Actual need is demand - (ready + building)
        for image_name in total_image_min_ready:
            start_demand = image_demand.get(image_name, 0)
            min_demand = max(start_demand, total_image_min_ready[image_name])
            n_ready = count_nodes(image_name, nodedb.READY)
            n_building = count_nodes(image_name, nodedb.BUILDING)
            n_test = count_nodes(image_name, nodedb.TEST)
            ready = n_ready + n_building + n_test
            demand = max(min_demand - ready, 0)
            image_demand[image_name] = demand
            self.log.debug("  Deficit: %s: %s (start: %s min: %s ready: %s)" %
                           (image_name, demand, start_demand, min_demand,
                            ready))

        # Start setting up the allocation system.  Add a provider for
        # each node provider, along with current capacity
        allocation_providers = {}
        for provider in self.config.providers.values():
            provider_max = provider.max_servers
            n_provider = len(session.getNodes(provider.name))
            available = provider_max - n_provider
            ap = allocation.AllocationProvider(provider.name, available)
            allocation_providers[provider.name] = ap

        # "Target-Image-Provider" -- the triplet of info that identifies
        # the source and location of each node.  The mapping is
        # AllocationGrantTarget -> TargetImageProvider, because
        # the allocation system produces AGTs as the final product.
        tips = {}
        # image_name -> AllocationRequest
        allocation_requests = {}
        # Set up the request values in the allocation system
        for target in self.config.targets.values():
            if not target.online:
                continue
            at = allocation.AllocationTarget(target.name)
            for image in target.images.values():
                ar = allocation_requests.get(image.name)
                if not ar:
                    # A request for a certain number of nodes of this
                    # image type.  We may have already started a
                    # request from a previous target-image in this
                    # loop.
                    ar = allocation.AllocationRequest(image.name,
                                                      image_demand[image.name])

                nodes = session.getNodes(image_name=image_name,
                                         target_name=target.name)
                allocation_requests[image.name] = ar
                ar.addTarget(at, image.min_ready, len(nodes))
                for provider in image.providers.values():
                    # This request may be supplied by this provider
                    # (and nodes from this provider supplying this
                    # request should be distributed to this target).
                    sr, agt = ar.addProvider(
                        allocation_providers[provider.name], at)
                    tips[agt] = provider

        self.log.debug("  Allocation requests:")
        for ar in allocation_requests.values():
            self.log.debug('    %s' % ar)
            for sr in ar.sub_requests.values():
                self.log.debug('      %s' % sr)

        nodes_to_launch = {}

        # Let the allocation system do it's thing, and then examine
        # the AGT objects that it produces.
        self.log.debug("  Grants:")
        for ap in allocation_providers.values():
            ap.makeGrants()
            for g in ap.grants:
                self.log.debug('    %s' % g)
                for agt in g.targets:
                    self.log.debug('      %s' % agt)
                    tip = tips[agt]
                    nodes_to_launch[tip] = agt.amount

        self.log.debug("Finished node launch calculation")
        return nodes_to_launch

    def run(self):
        while not self._stopped:
            try:
                config = self.loadConfig()
                self.reconfigureDatabase(config)
                self.reconfigureManagers(config)
                self.reconfigureCrons(config)
                self.reconfigureUpdateListeners(config)
                self.reconfigureGearmanClient(config)
                self.setConfig(config)

                with self.getDB().getSession() as session:
                    self._run(session)
            except Exception:
                self.log.exception("Exception in main loop:")
            time.sleep(WATERMARK_SLEEP)

    def _run(self, session):
        self.checkForMissingImages(session)
        nodes_to_launch = self.getNeededNodes(session)
        for target in self.config.targets.values():
            if not target.online:
                continue
            self.log.debug("Examining target: %s" % target.name)
            for image in target.images.values():
                for provider in image.providers.values():
                    num_to_launch = nodes_to_launch.get(provider, 0)
                    if num_to_launch:
                        self.log.info("Need to launch %s %s nodes for "
                                      "%s on %s" %
                                      (num_to_launch, image.name,
                                       target.name, provider.name))
                    for i in range(num_to_launch):
                        snap_image = session.getCurrentSnapshotImage(
                            provider.name, image.name)
                        if not snap_image:
                            self.log.debug("No current image for %s on %s"
                                           % (image.name, provider.name))
                        else:
                            self.launchNode(session, provider, image, target)

    def checkForMissingImages(self, session):
        # If we are missing an image, run the image update function
        # outside of its schedule.
        self.log.debug("Checking missing images.")
        for target in self.config.targets.values():
            self.log.debug("Checking target: %s", target.name)
            for image in target.images.values():
                self.log.debug("Checking image: %s", image.name)
                for provider in image.providers.values():
                    found = False
                    for snap_image in session.getSnapshotImages():
                        if (snap_image.provider_name == provider.name and
                            snap_image.image_name == image.name and
                            snap_image.state in [nodedb.READY,
                                                 nodedb.BUILDING]):
                            found = True
                            self.log.debug('Found image %s in state %r',
                                           snap_image.image_name,
                                           snap_image.state)
                    if not found:
                        self.log.warning("Missing image %s on %s" %
                                         (image.name, provider.name))
                        self.updateImage(session, provider, image)

    def _doUpdateImages(self):
        try:
            with self.getDB().getSession() as session:
                self.updateImages(session)
        except Exception:
            self.log.exception("Exception in periodic image update:")

    def updateImages(self, session):
        # This function should be run periodically to create new snapshot
        # images.
        for provider in self.config.providers.values():
            for image in provider.images.values():
                self.updateImage(session, provider, image)

    def updateImage(self, session, provider, image):
        try:
            self._updateImage(session, provider, image)
        except Exception:
            self.log.exception(
                "Could not update image %s on %s", image.name, provider.name)

    def _updateImage(self, session, provider, image):
        provider = self.config.providers[provider.name]
        image = provider.images[image.name]
        snap_image = session.createSnapshotImage(
            provider_name=provider.name,
            image_name=image.name)
        t = ImageUpdater(self, provider, image, snap_image.id)
        t.start()
        # Enough time to give them different timestamps (versions)
        # Just to keep things clearer.
        time.sleep(2)
        return t

    def launchNode(self, session, provider, image, target):
        try:
            self._launchNode(session, provider, image, target)
        except Exception:
            self.log.exception(
                "Could not launch node %s on %s", image.name, provider.name)

    def _launchNode(self, session, provider, image, target):
        provider = self.config.providers[provider.name]
        image = provider.images[image.name]
        timeout = provider.boot_timeout
        node = session.createNode(provider.name, image.name, target.name)
        t = NodeLauncher(self, provider, image, target, node.id, timeout)
        t.start()

    def deleteNode(self, session, node):
        # Delete a node
        if node.state != nodedb.DELETE:
            # Don't write to the session if not needed.
            node.state = nodedb.DELETE
        self.updateStats(session, node.provider_name)
        provider = self.config.providers[node.provider_name]
        target = self.config.targets[node.target_name]
        manager = self.getProviderManager(provider)

        if target.jenkins_url:
            jenkins = self.getJenkinsManager(target)
            jenkins_name = node.nodename
            if jenkins.nodeExists(jenkins_name):
                jenkins.deleteNode(jenkins_name)
            self.log.info("Deleted jenkins node id: %s" % node.id)

        if node.external_id:
            try:
                server = manager.getServer(node.external_id)
                self.log.debug('Deleting server %s for node id: %s' %
                               (node.external_id,
                                node.id))
                manager.cleanupServer(server['id'])
            except provider_manager.NotFound:
                pass

        node.delete()
        self.log.info("Deleted node id: %s" % node.id)

        if statsd:
            dt = int((time.time() - node.state_time) * 1000)
            key = 'nodepool.delete.%s.%s.%s' % (node.image_name,
                                                node.provider_name,
                                                node.target_name)
            statsd.timing(key, dt)
            statsd.incr(key)
        self.updateStats(session, node.provider_name)

    def deleteImage(self, snap_image):
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
        node_ids = []
        image_ids = []
        with self.getDB().getSession() as session:
            for node in session.getNodes():
                node_ids.append(node.id)
            for image in session.getSnapshotImages():
                image_ids.append(image.id)

        for node_id in node_ids:
            try:
                with self.getDB().getSession() as session:
                    node = session.getNode(node_id)
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
        self.log.debug("Finished periodic cleanup")

    def cleanupOneNode(self, session, node):
        now = time.time()
        time_in_state = now - node.state_time
        if (node.state in [nodedb.READY, nodedb.HOLD] or
            time_in_state < 900):
            return
        delete = False
        if (node.state == nodedb.DELETE):
            self.log.warning("Deleting node id: %s which is in delete "
                             "state" % node.id)
            delete = True
        elif (node.state == nodedb.TEST and
              time_in_state > TEST_CLEANUP):
            self.log.warning("Deleting node id: %s which has been in %s "
                             "state for %s hours" %
                             (node.id, node.state,
                              (now - node.state_time) / (60 * 60)))
            delete = True
        elif time_in_state > NODE_CLEANUP:
            self.log.warning("Deleting node id: %s which has been in %s "
                             "state for %s hours" %
                             (node.id, node.state,
                              time_in_state / (60 * 60)))
            delete = True
        if delete:
            try:
                self.deleteNode(session, node)
            except Exception:
                self.log.exception("Exception deleting node id: "
                                   "%s" % node.id)

    def cleanupOneImage(self, session, image):
        # Normally, reap images that have sat in their current state
        # for 24 hours, unless the image is the current snapshot
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
            current = session.getCurrentSnapshotImage(image.provider_name,
                                                      image.image_name)
            if (current and image != current and
                (now - image.state_time) > KEEP_OLD_IMAGE):
                self.log.info("Deleting non-current image id: %s because "
                              "the image is %s hours old" %
                              (image.id,
                               (now - image.state_time) / (60 * 60)))
                delete = True
        if delete:
            try:
                self.deleteImage(image)
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
            try:
                provider = self.config.providers[node.provider_name]
                image = provider.images[node.image_name]
                connect_kwargs = dict(key_filename=image.private_key)
                if utils.ssh_connect(node.ip, image.username,
                                     connect_kwargs=connect_kwargs):
                    continue
            except Exception:
                self.log.exception("SSH Check failed for node id: %s" %
                                   node.id)
                self.deleteNode(session, node)
        self.log.debug("Finished periodic check")

    def updateStats(self, session, provider_name):
        if not statsd:
            return
        # This may be called outside of the main thread.
        provider = self.config.providers[provider_name]

        states = {}

        for target in self.config.targets.values():
            for image in target.images.values():
                image_key = 'nodepool.target.%s.%s' % (
                    target.name, image.name)
                key = '%s.min_ready' % image_key
                statsd.gauge(key, image.min_ready)
                for provider in image.providers.values():
                    provider_key = '%s.%s' % (
                        image_key, provider.name)
                    for state in nodedb.STATE_NAMES.values():
                        key = '%s.%s' % (provider_key, state)
                        states[key] = 0

        for node in session.getNodes():
            if node.state not in nodedb.STATE_NAMES:
                continue
            key = 'nodepool.target.%s.%s.%s.%s' % (
                node.target_name, node.image_name,
                node.provider_name, nodedb.STATE_NAMES[node.state])
            if key not in states:
                states[key] = 0
            states[key] += 1

        for key, count in states.items():
            statsd.gauge(key, count)

        for provider in self.config.providers.values():
            key = 'nodepool.provider.%s.max_servers' % provider.name
            statsd.gauge(key, provider.max_servers)

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

import logging
import time
import json
import threading
import yaml
import apscheduler.scheduler
import os
import myjenkins
from statsd import statsd
import zmq

import nodedb
import nodeutils as utils

MINS = 60
HOURS = 60 * MINS

WATERMARK_SLEEP = 5          # Interval between checking if new servers needed
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
CONNECT_TIMEOUT = 10 * MINS  # How long to try to connect after a server
                             # is ACTIVE
NODE_CLEANUP = 8 * HOURS     # When to start deleting a node that is not
                             # READY or HOLD
KEEP_OLD_IMAGE = 24 * HOURS  # How long to keep an old (good) image


class NodeCompleteThread(threading.Thread):
    log = logging.getLogger("nodepool.NodeCompleteThread")

    def __init__(self, nodepool, nodename):
        threading.Thread.__init__(self)
        self.nodename = nodename
        self.nodepool = nodepool
        self.db = nodedb.NodeDatabase(self.nodepool.config.dburi)

    def run(self):
        try:
            self.handleEvent()
        except Exception:
            self.log.exception("Exception handling event for %s:" %
                               self.nodename)

    def handleEvent(self):
        node = self.db.getNodeByNodename(self.nodename)
        if not node:
            self.log.debug("Unable to find node with nodename: %s" %
                           self.nodename)
            return
        self.nodepool.deleteNode(node)


class NodeUpdateListener(threading.Thread):
    log = logging.getLogger("nodepool.NodeUpdateListener")

    def __init__(self, nodepool, addr):
        threading.Thread.__init__(self)
        self.nodepool = nodepool
        self.socket = self.nodepool.zmq_context.socket(zmq.SUB)
        event_filter = b""
        self.socket.setsockopt(zmq.SUBSCRIBE, event_filter)
        self.socket.connect(addr)
        self._stopped = False
        self.db = nodedb.NodeDatabase(self.nodepool.config.dburi)

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
        nodename = args['build']['node_name']
        if topic == 'onStarted':
            self.handleStartPhase(nodename)
        elif topic == 'onCompleted':
            pass
        elif topic == 'onFinalized':
            self.handleCompletePhase(nodename)
        else:
            raise Exception("Received job for unhandled phase: %s" %
                            topic)

    def handleStartPhase(self, nodename):
        node = self.db.getNodeByNodename(nodename)
        if not node:
            self.log.debug("Unable to find node with nodename: %s" %
                           nodename)
            return
        node.state = nodedb.USED
        self.nodepool.updateStats(node.provider_name)

    def handleCompletePhase(self, nodename):
        t = NodeCompleteThread(self.nodepool, nodename)
        t.start()


class NodeLauncher(threading.Thread):
    log = logging.getLogger("nodepool.NodeLauncher")

    def __init__(self, nodepool, provider, image, target, node_id):
        threading.Thread.__init__(self)
        self.provider = provider
        self.image = image
        self.target = target
        self.node_id = node_id
        self.nodepool = nodepool

    def run(self):
        self.log.debug("Launching node id: %s" % self.node_id)
        try:
            self.db = nodedb.NodeDatabase(self.nodepool.config.dburi)
            self.node = self.db.getNode(self.node_id)
            self.client = utils.get_client(self.provider)
        except Exception:
            self.log.exception("Exception preparing to launch node id: %s:" %
                               self.node_id)
            return

        try:
            self.launchNode()
        except Exception:
            self.log.exception("Exception launching node id: %s:" %
                               self.node_id)
            try:
                utils.delete_node(self.client, self.node)
            except Exception:
                self.log.exception("Exception deleting node id: %s:" %
                                   self.node_id)
                return

    def launchNode(self):
        start_time = time.time()

        hostname = '%s-%s-%s.slave.openstack.org' % (
            self.image.name, self.provider.name, self.node.id)
        self.node.hostname = hostname
        self.node.nodename = hostname.split('.')[0]
        self.node.target_name = self.target.name

        flavor = utils.get_flavor(self.client, self.image.min_ram)
        snap_image = self.db.getCurrentSnapshotImage(
            self.provider.name, self.image.name)
        if not snap_image:
            raise Exception("Unable to find current snapshot image %s in %s" %
                            (self.image.name, self.provider.name))
        remote_snap_image = self.client.images.get(snap_image.external_id)

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self.provider.name,
                                           self.image.name, self.node_id))
        server = None
        server, key = utils.create_server(self.client, hostname,
                                          remote_snap_image, flavor)
        self.node.external_id = server.id
        self.db.commit()

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server.id, self.node.id))

        server = utils.wait_for_resource(server)
        if server.status != 'ACTIVE':
            raise Exception("Server %s for node id: %s status: %s" %
                            (server.id, self.node.id, server.status))

        ip = utils.get_public_ip(server)
        if not ip and 'os-floating-ips' in utils.get_extensions(self.client):
            utils.add_public_ip(server)
            ip = utils.get_public_ip(server)
        if not ip:
            raise Exception("Unable to find public ip of server")

        self.node.ip = ip
        self.log.debug("Node id: %s is running, testing ssh" % self.node.id)
        if not utils.ssh_connect(ip, 'jenkins'):
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
        self.node.state = nodedb.READY
        self.nodepool.updateStats(self.provider.name)
        self.log.info("Node id: %s is ready" % self.node.id)

        if self.target.jenkins_url:
            self.log.debug("Adding node id: %s to jenkins" % self.node.id)
            self.createJenkinsNode()
            self.log.info("Node id: %s added to jenkins" % self.node.id)

    def createJenkinsNode(self):
        jenkins = myjenkins.Jenkins(self.target.jenkins_url,
                                    self.target.jenkins_user,
                                    self.target.jenkins_apikey)
        node_desc = 'Dynamic single use %s node' % self.image.name
        labels = self.image.name
        priv_key = '/var/lib/jenkins/.ssh/id_rsa'
        if self.target.jenkins_credentials_id:
            launcher_params = {'port': 22,
                               'credentialsId':
                                   self.target.jenkins_credentials_id,  # noqa
                               'host': self.node.ip}
        else:
            launcher_params = {'port': 22,
                               'username': 'jenkins',
                               'privatekey': priv_key,
                               'host': self.node.ip}
        try:
            jenkins.create_node(
                self.node.nodename,
                numExecutors=1,
                nodeDescription=node_desc,
                remoteFS='/home/jenkins',
                labels=labels,
                exclusive=True,
                launcher='hudson.plugins.sshslaves.SSHLauncher',
                launcher_params=launcher_params)
        except myjenkins.JenkinsException as e:
            if 'already exists' in str(e):
                pass
            else:
                raise


class ImageUpdater(threading.Thread):
    log = logging.getLogger("nodepool.ImageUpdater")

    def __init__(self, nodepool, provider, image, snap_image_id):
        threading.Thread.__init__(self)
        self.provider = provider
        self.image = image
        self.snap_image_id = snap_image_id
        self.nodepool = nodepool
        self.scriptdir = self.nodepool.config.scriptdir

    def run(self):
        self.log.debug("Updating image %s in %s " % (self.image.name,
                                                     self.provider.name))
        try:
            self.db = nodedb.NodeDatabase(self.nodepool.config.dburi)
            self.snap_image = self.db.getSnapshotImage(self.snap_image_id)
            self.client = utils.get_client(self.provider)
        except Exception:
            self.log.exception("Exception preparing to update image %s in %s:"
                               % (self.image.name, self.provider.name))
            return

        try:
            self.updateImage()
        except Exception:
            self.log.exception("Exception updating image %s in %s:" %
                               (self.image.name, self.provider.name))
            try:
                if self.snap_image:
                    utils.delete_image(self.client, self.snap_image)
            except Exception:
                self.log.exception("Exception deleting image id: %s:" %
                                   self.snap_image.id)
                return

    def updateImage(self):
        start_time = time.time()
        timestamp = int(start_time)

        flavor = utils.get_flavor(self.client, self.image.min_ram)
        base_image = self.client.images.find(name=self.image.base_image)
        hostname = ('%s-%s.template.openstack.org' %
                    (self.image.name, str(timestamp)))
        self.log.info("Creating image id: %s for %s in %s" %
                      (self.snap_image.id, self.image.name,
                       self.provider.name))
        server, key = utils.create_server(
            self.client, hostname, base_image, flavor, add_key=True)

        self.snap_image.hostname = hostname
        self.snap_image.version = timestamp
        self.snap_image.server_external_id = server.id
        self.db.commit()

        self.log.debug("Image id: %s waiting for server %s" %
                       (self.snap_image.id, server.id))
        server = utils.wait_for_resource(server)
        if not server:
            raise Exception("Timeout waiting for server %s" %
                            server.id)

        admin_pass = None  # server.adminPass
        self.bootstrapServer(server, admin_pass, key)

        image = utils.create_image(self.client, server, hostname)
        self.snap_image.external_id = image.id
        self.db.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image.id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        image = utils.wait_for_resource(image, IMAGE_TIMEOUT)
        if not image:
            raise Exception("Timeout waiting for image %s" %
                            self.snap_image.id)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (self.image.name,
                                                   self.provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        self.snap_image.state = nodedb.READY
        self.log.info("Image %s in %s is ready" % (hostname,
                                                   self.provider.name))

        try:
            # We made the snapshot, try deleting the server, but it's okay
            # if we fail.  The reap script will find it and try again.
            utils.delete_server(self.client, server)
        except:
            self.log.exception("Exception encountered deleting server"
                               " %s for image id: %s" %
                               (server.id, self.snap_image.id))

    def bootstrapServer(self, server, admin_pass, key):
        ip = utils.get_public_ip(server)
        if not ip and 'os-floating-ips' in utils.get_extensions(self.client):
            utils.add_public_ip(server)
            ip = utils.get_public_ip(server)
        if not ip:
            raise Exception("Unable to find public ip of server")

        ssh_kwargs = {}
        if key:
            ssh_kwargs['pkey'] = key
        else:
            ssh_kwargs['password'] = admin_pass

        for username in ['root', 'ubuntu']:
            host = utils.ssh_connect(ip, username, ssh_kwargs,
                                     timeout=CONNECT_TIMEOUT)
            if host:
                break

        if not host:
            raise Exception("Unable to log in via SSH")

        host.ssh("make scripts dir", "mkdir -p scripts")
        for fname in os.listdir(self.scriptdir):
            host.scp(os.path.join(self.scriptdir, fname), 'scripts/%s' % fname)
        host.ssh("make scripts executable", "chmod a+x scripts/*")
        if self.image.setup:
            env_vars = ''
            for k, v in os.environ.items():
                if k.startswith('NODEPOOL_'):
                    env_vars += ' %s="%s"' % (k, v)
            r = host.ssh("run setup script", "cd scripts && %s ./%s" %
                         (env_vars, self.image.setup))
            if not r:
                raise Exception("Unable to run setup scripts")


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


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, configfile):
        threading.Thread.__init__(self)
        self.configfile = configfile
        self.zmq_context = None
        self.zmq_listeners = {}
        self.db = None
        self.apsched = apscheduler.scheduler.Scheduler()
        self.apsched.start()

        self.update_cron = ''
        self.update_job = None
        self.cleanup_cron = ''
        self.cleanup_job = None
        self._stopped = False
        self.loadConfig()

    def stop(self):
        self._stopped = True
        self.zmq_context.destroy()
        self.apsched.shutdown()

    def loadConfig(self):
        self.log.debug("Loading configuration")
        config = yaml.load(open(self.configfile))

        update_cron = config.get('cron', {}).get('image-update', '14 2 * * *')
        cleanup_cron = config.get('cron', {}).get('cleanup', '27 */6 * * *')
        if (update_cron != self.update_cron):
            if self.update_job:
                self.apsched.unschedule_job(self.update_job)
            parts = update_cron.split()
            minute, hour, dom, month, dow = parts[:5]
            self.apsched.add_cron_job(self.updateImages,
                                      day=dom,
                                      day_of_week=dow,
                                      hour=hour,
                                      minute=minute)
            self.update_cron = update_cron
        if (cleanup_cron != self.cleanup_cron):
            if self.cleanup_job:
                self.apsched.unschedule_job(self.cleanup_job)
            parts = cleanup_cron.split()
            minute, hour, dom, month, dow = parts[:5]
            self.apsched.add_cron_job(self.periodicCleanup,
                                      day=dom,
                                      day_of_week=dow,
                                      hour=hour,
                                      minute=minute)
            self.cleanup_cron = cleanup_cron

        newconfig = Config()
        newconfig.providers = {}
        newconfig.targets = {}
        newconfig.scriptdir = config.get('script-dir')
        newconfig.dburi = config.get('dburi')

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
            p.images = {}
            for image in provider['images']:
                i = ProviderImage()
                i.name = image['name']
                p.images[i.name] = i
                i.base_image = image['base-image']
                i.min_ram = image['min-ram']
                i.setup = image.get('setup')
                i.reset = image.get('reset')
        for target in config['targets']:
            t = Target()
            t.name = target['name']
            newconfig.targets[t.name] = t
            jenkins = target.get('jenkins')
            if jenkins:
                t.jenkins_url = jenkins['url']
                t.jenkins_user = jenkins['user']
                t.jenkins_apikey = jenkins['apikey']
                t.jenkins_credentials_id = jenkins.get('credentials_id')
            else:
                t.jenkins_url = None
                t.jenkins_user = None
                t.jenkins_apikey = None
                t.jenkins_credentials_id = None
            t.images = {}
            for image in target['images']:
                i = TargetImage()
                i.name = image['name']
                t.images[i.name] = i
                i.providers = {}
                for provider in image['providers']:
                    p = TargetImageProvider()
                    p.name = provider['name']
                    i.providers[p.name] = p
                    p.min_ready = provider['min-ready']
        self.config = newconfig
        self.db = nodedb.NodeDatabase(self.config.dburi)
        self.startUpdateListeners(config['zmq-publishers'])

    def startUpdateListeners(self, publishers):
        running = set(self.zmq_listeners.keys())
        configured = set(publishers)
        if running == configured:
            self.log.debug("Listeners do not need to be updated")
            return

        if self.zmq_context:
            self.log.debug("Stopping listeners")
            self.zmq_context.destroy()
            self.zmq_listeners = {}
        self.zmq_context = zmq.Context()
        for addr in publishers:
            self.log.debug("Starting listener for %s" % addr)
            listener = NodeUpdateListener(self, addr)
            self.zmq_listeners[addr] = listener
            listener.start()

    def getNumNeededNodes(self, target, provider, image):
        # Count machines that are ready and machines that are building,
        # so that if the provider is very slow, we aren't queueing up tons
        # of machines to be built.
        n_ready = len(self.db.getNodes(provider.name, image.name, target.name,
                                       nodedb.READY))
        n_building = len(self.db.getNodes(provider.name, image.name,
                                          target.name, nodedb.BUILDING))
        n_provider = len(self.db.getNodes(provider.name))
        num_to_launch = provider.min_ready - (n_ready + n_building)

        # Don't launch more than our provider max
        max_servers = self.config.providers[provider.name].max_servers
        num_to_launch = min(max_servers - n_provider, num_to_launch)

        # Don't launch less than 0
        num_to_launch = max(0, num_to_launch)

        return num_to_launch

    def run(self):
        while not self._stopped:
            self.loadConfig()
            self.checkForMissingImages()
            for target in self.config.targets.values():
                self.log.debug("Examining target: %s" % target.name)
                for image in target.images.values():
                    for provider in image.providers.values():
                        num_to_launch = self.getNumNeededNodes(
                            target, provider, image)
                        if num_to_launch:
                            self.log.info("Need to launch %s %s nodes for "
                                          "%s on %s" %
                                          (num_to_launch, image.name,
                                           target.name, provider.name))
                        for i in range(num_to_launch):
                            snap_image = self.db.getCurrentSnapshotImage(
                                provider.name, image.name)
                            if not snap_image:
                                self.log.debug("No current image for %s on %s"
                                               % (provider.name, image.name))
                            else:
                                self.launchNode(provider, image, target)
            time.sleep(WATERMARK_SLEEP)

    def checkForMissingImages(self):
        # If we are missing an image, run the image update function
        # outside of its schedule.
        missing = False
        for target in self.config.targets.values():
            for image in target.images.values():
                for provider in image.providers.values():
                    found = False
                    for snap_image in self.db.getSnapshotImages():
                        if (snap_image.provider_name == provider.name and
                            snap_image.image_name == image.name and
                            snap_image.state in [nodedb.READY,
                                                 nodedb.BUILDING]):
                            found = True
                    if not found:
                        self.log.warning("Missing image %s on %s" %
                                         (image.name, provider.name))
                        missing = True
        if missing:
            self.updateImages()

    def updateImages(self):
        # This function should be run periodically to create new snapshot
        # images.
        for provider in self.config.providers.values():
            for image in provider.images.values():
                snap_image = self.db.createSnapshotImage(
                    provider_name=provider.name,
                    image_name=image.name)
                t = ImageUpdater(self, provider, image, snap_image.id)
                t.start()
                # Enough time to give them different timestamps (versions)
                # Just to keep things clearer.
                time.sleep(2)

    def launchNode(self, provider, image, target):
        provider = self.config.providers[provider.name]
        image = provider.images[image.name]
        node = self.db.createNode(provider.name, image.name)
        t = NodeLauncher(self, provider, image, target, node.id)
        t.start()

    def deleteNode(self, node):
        # Delete a node
        start_time = time.time()
        node.state = nodedb.DELETE
        self.updateStats(node.provider_name)
        provider = self.config.providers[node.provider_name]
        target = self.config.targets[node.target_name]
        client = utils.get_client(provider)

        if target.jenkins_url:
            jenkins = myjenkins.Jenkins(target.jenkins_url,
                                        target.jenkins_user,
                                        target.jenkins_apikey)
            jenkins_name = node.nodename
            if jenkins.node_exists(jenkins_name):
                jenkins.delete_node(jenkins_name)
            self.log.info("Deleted jenkins node ID: %s" % node.id)

        utils.delete_node(client, node)
        self.log.info("Deleted node ID: %s" % node.id)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.delete.%s.%s.%s' % (node.image_name,
                                                node.provider_name,
                                                node.target_name)
            statsd.timing(key, dt)
            statsd.incr(key)
        self.updateStats(node.provider_name)

    def deleteImage(self, snap_image):
        # Delete a node
        snap_image.state = nodedb.DELETE
        provider = self.config.providers[snap_image.provider_name]
        client = utils.get_client(provider)

        utils.delete_image(client, snap_image)
        self.log.info("Deleted image ID: %s" % snap_image.id)

    def periodicCleanup(self):
        # This function should be run periodically to clean up any hosts
        # that may have slipped through the cracks, as well as to remove
        # old images.

        self.log.debug("Starting periodic cleanup")
        db = nodedb.NodeDatabase(self.config.dburi)
        for node in db.getNodes():
            if node.state in [nodedb.READY, nodedb.HOLD]:
                continue
            delete = False
            if (node.state == nodedb.DELETE):
                self.log.warning("Deleting node id: %s which is in delete "
                                 "state" % node.id)
                delete = True
            elif time.time() - node.state_time > NODE_CLEANUP:
                self.log.warning("Deleting node id: %s which has been in %s "
                                 "state for %s hours" %
                                 (node.id, node.state,
                                  node.state_time / (60 * 60)))
                delete = True
            if delete:
                try:
                    self.deleteNode(node)
                except Exception:
                    self.log.exception("Exception deleting node ID: "
                                       "%s" % node.id)

        for image in db.getSnapshotImages():
            # Normally, reap images that have sat in their current state
            # for 24 hours, unless the image is the current snapshot
            delete = False
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
                current = db.getCurrentSnapshotImage(image.provider_name,
                                                     image.image_name)
                if (current and image != current and
                    (time.time() - current.state_time) > KEEP_OLD_IMAGE):
                    self.log.info("Deleting image id: %s because the current "
                                  "image is %s hours old" %
                                  (image.id, current.state_time / (60 * 60)))
                    delete = True
            if delete:
                try:
                    self.deleteImage(image)
                except Exception:
                    self.log.exception("Exception deleting image id: %s:" %
                                       image.id)
        self.log.debug("Finished periodic cleanup")

    def updateStats(self, provider_name):
        if not statsd:
            return
        # This may be called outside of the main thread.
        db = nodedb.NodeDatabase(self.config.dburi)
        provider = self.config.providers[provider_name]

        states = {}

        for target in self.config.targets.values():
            for image in target.images.values():
                for provider in image.providers.values():
                    base_key = 'nodepool.target.%s.%s.%s' % (
                        target.name, image.name,
                        provider.name)
                    key = '%s.min_ready' % base_key
                    statsd.gauge(key, provider.min_ready)
                    for state in nodedb.STATE_NAMES.values():
                        key = '%s.%s' % (base_key, state)
                        states[key] = 0

        for node in db.getNodes():
            if node.state not in nodedb.STATE_NAMES:
                continue
            key = 'nodepool.target.%s.%s.%s.%s' % (
                node.target_name, node.image_name,
                node.provider_name, nodedb.STATE_NAMES[node.state])
            states[key] += 1

        for key, count in states.items():
            statsd.gauge(key, count)

        for provider in self.config.providers.values():
            key = 'nodepool.provider.%s.max_servers' % provider.name
            statsd.gauge(key, provider.max_servers)

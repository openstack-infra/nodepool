# Copyright (C) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (C) 2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Common utilities used in testing"""

import errno
import logging
import os
import pymysql
import random
import re
import string
import subprocess
import threading
import tempfile
import time
import uuid

import fixtures
import gear
import lockfile
import kazoo.client
import testtools

from nodepool import allocation, builder, fakeprovider, nodepool, nodedb, webapp
from nodepool import zk

TRUE_VALUES = ('true', '1', 'yes')


class LoggingPopen(subprocess.Popen):
    pass


class FakeGearmanServer(gear.Server):
    def __init__(self, port=0):
        self.hold_jobs_in_queue = False
        super(FakeGearmanServer, self).__init__(port)

    def getJobForConnection(self, connection, peek=False):
        for queue in [self.high_queue, self.normal_queue, self.low_queue]:
            for job in queue:
                if not hasattr(job, 'waiting'):
                    if job.name.startswith('build:'):
                        job.waiting = self.hold_jobs_in_queue
                    else:
                        job.waiting = False
                if job.waiting:
                    continue
                if job.name in connection.functions:
                    if not peek:
                        queue.remove(job)
                        connection.related_jobs[job.handle] = job
                        job.worker_connection = connection
                    job.running = True
                    return job
        return None

    def release(self, regex=None):
        released = False
        qlen = (len(self.high_queue) + len(self.normal_queue) +
                len(self.low_queue))
        self.log.debug("releasing queued job %s (%s)" % (regex, qlen))
        for job in self.getQueue():
            cmd, name = job.name.split(':')
            if cmd != 'build':
                continue
            if not regex or re.match(regex, name):
                self.log.debug("releasing queued job %s" %
                               job.unique)
                job.waiting = False
                released = True
            else:
                self.log.debug("not releasing queued job %s" %
                               job.unique)
        if released:
            self.wakeConnections()
        qlen = (len(self.high_queue) + len(self.normal_queue) +
                len(self.low_queue))
        self.log.debug("done releasing queued jobs %s (%s)" % (regex, qlen))


class GearmanServerFixture(fixtures.Fixture):
    def __init__(self, port=0):
        self._port = port

    def setUp(self):
        super(GearmanServerFixture, self).setUp()
        self.gearman_server = FakeGearmanServer(self._port)
        self.addCleanup(self.shutdownGearman)

    def shutdownGearman(self):
        #TODO:greghaynes remove try once gear client protects against this
        try:
            self.gearman_server.shutdown()
        except OSError as e:
            if e.errno == errno.EBADF:
                pass
            else:
                raise


class ZookeeperServerFixture(fixtures.Fixture):
    def _setUp(self):
        zk_host = os.environ.get('NODEPOOL_ZK_HOST', 'localhost')
        if ':' in zk_host:
            host, port = zk_host.split(':')
        else:
            host = zk_host
            port = None

        self.zookeeper_host = host

        if not port:
            self.zookeeper_port = 2181
        else:
            self.zookeeper_port = int(port)


class ChrootedKazooFixture(fixtures.Fixture):
    def __init__(self, zookeeper_host, zookeeper_port):
        super(ChrootedKazooFixture, self).__init__()
        self.zookeeper_host = zookeeper_host
        self.zookeeper_port = zookeeper_port

    def _setUp(self):
        # Make sure the test chroot paths do not conflict
        random_bits = ''.join(random.choice(string.ascii_lowercase +
                                            string.ascii_uppercase)
                              for x in range(8))

        rand_test_path = '%s_%s' % (random_bits, os.getpid())
        self.chroot_path = "/nodepool_test/%s" % rand_test_path

        # Ensure the chroot path exists and clean up an pre-existing znodes.
        _tmp_client = kazoo.client.KazooClient(
            hosts='%s:%s' % (self.zookeeper_host, self.zookeeper_port))
        _tmp_client.start()

        if _tmp_client.exists(self.chroot_path):
            _tmp_client.delete(self.chroot_path, recursive=True)

        _tmp_client.ensure_path(self.chroot_path)
        _tmp_client.stop()

        # Create a chroot'ed client
        self.zkclient = kazoo.client.KazooClient(
            hosts='%s:%s%s' % (self.zookeeper_host,
                               self.zookeeper_port,
                               self.chroot_path)
        )
        self.zkclient.start()

        self.addCleanup(self._cleanup)

    def _cleanup(self):
        '''Stop the client and remove the chroot path.'''
        self.zkclient.stop()

        # Need a non-chroot'ed client to remove the chroot path
        _tmp_client = kazoo.client.KazooClient(
            hosts='%s:%s' % (self.zookeeper_host, self.zookeeper_port))
        _tmp_client.start()
        _tmp_client.delete(self.chroot_path, recursive=True)
        _tmp_client.stop()


class GearmanClient(gear.Client):
    def __init__(self):
        super(GearmanClient, self).__init__(client_id='test_client')
        self.__log = logging.getLogger("tests.GearmanClient")

    def get_queued_image_jobs(self):
        'Count the number of image-build and upload jobs queued.'
        queued = 0
        for connection in self.active_connections:
            try:
                req = gear.StatusAdminRequest()
                connection.sendAdminRequest(req)
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
                if (not parts[0].startswith('image-build:') and
                    not parts[0].startswith('image-upload:')):
                    continue
                queued += int(parts[1])
        return queued


class BaseTestCase(testtools.TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()
        test_timeout = os.environ.get('OS_TEST_TIMEOUT', 60)
        try:
            test_timeout = int(test_timeout)
        except ValueError:
            # If timeout value is invalid, fail hard.
            print("OS_TEST_TIMEOUT set to invalid value"
                  " defaulting to no timeout")
            test_timeout = 0
        if test_timeout > 0:
            self.useFixture(fixtures.Timeout(test_timeout, gentle=True))

        if os.environ.get('OS_STDOUT_CAPTURE') in TRUE_VALUES:
            stdout = self.useFixture(fixtures.StringStream('stdout')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stdout', stdout))
        if os.environ.get('OS_STDERR_CAPTURE') in TRUE_VALUES:
            stderr = self.useFixture(fixtures.StringStream('stderr')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stderr', stderr))
        if os.environ.get('OS_LOG_CAPTURE') in TRUE_VALUES:
            fs = '%(levelname)s [%(name)s] %(message)s'
            self.useFixture(fixtures.FakeLogger(level=logging.DEBUG,
                                                format=fs))
        else:
            logging.basicConfig(level=logging.DEBUG)
        l = logging.getLogger('kazoo')
        l.setLevel(logging.INFO)
        l.propagate=False
        self.useFixture(fixtures.NestedTempfile())

        self.subprocesses = []

        def LoggingPopenFactory(*args, **kw):
            p = LoggingPopen(*args, **kw)
            self.subprocesses.append(p)
            return p

        self.useFixture(fixtures.MonkeyPatch('subprocess.Popen',
                                             LoggingPopenFactory))
        self.setUpFakes()

    def setUpFakes(self):
        log = logging.getLogger("nodepool.test")
        log.debug("set up fakes")
        fake_client = fakeprovider.FakeOpenStackCloud()

        def get_fake_client(*args, **kwargs):
            return fake_client

        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.provider_manager.ProviderManager._getClient',
            get_fake_client))
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.nodepool._get_one_cloud',
            fakeprovider.fake_get_one_cloud))

    def wait_for_threads(self):
        whitelist = ['APScheduler',
                     'MainThread',
                     'NodePool',
                     'NodePool Builder',
                     'NodeUpdateListener',
                     'Gearman client connect',
                     'Gearman client poll',
                     'fake-provider',
                     'fake-provider1',
                     'fake-provider2',
                     'fake-provider3',
                     'fake-dib-provider',
                     'fake-jenkins',
                     'fake-target',
                     'DiskImageBuilder queue',
                     ]

        while True:
            done = True
            for t in threading.enumerate():
                if t.name.startswith("Thread-"):
                    # apscheduler thread pool
                    continue
                if t.name.startswith("worker "):
                    # paste web server
                    continue
                if t.name.startswith("UploadWorker"):
                    continue
                if t.name.startswith("BuildWorker"):
                    continue
                if t.name.startswith("CleanupWorker"):
                    continue
                if t.name not in whitelist:
                    done = False
            if done:
                return
            time.sleep(0.1)


class AllocatorTestCase(object):
    def setUp(self):
        super(AllocatorTestCase, self).setUp()
        self.agt = []

    def test_allocator(self):
        for i, amount in enumerate(self.results):
            print self.agt[i]
        for i, amount in enumerate(self.results):
            self.assertEqual(self.agt[i].amount, amount,
                             'Error at pos %d, '
                             'expected %s and got %s' % (i, self.results,
                                                         [x.amount
                                                          for x in self.agt]))


class RoundRobinTestCase(object):
    def setUp(self):
        super(RoundRobinTestCase, self).setUp()
        self.allocations = []

    def test_allocator(self):
        for i, label in enumerate(self.results):
            self.assertEqual(self.results[i], self.allocations[i],
                             'Error at pos %d, '
                             'expected %s and got %s' % (i, self.results,
                                                         self.allocations))


class MySQLSchemaFixture(fixtures.Fixture):
    def setUp(self):
        super(MySQLSchemaFixture, self).setUp()

        random_bits = ''.join(random.choice(string.ascii_lowercase +
                                            string.ascii_uppercase)
                              for x in range(8))
        self.name = '%s_%s' % (random_bits, os.getpid())
        self.passwd = uuid.uuid4().hex
        lock = lockfile.LockFile('/tmp/nodepool-db-schema-lockfile')
        with lock:
            db = pymysql.connect(host="localhost",
                                 user="openstack_citest",
                                 passwd="openstack_citest",
                                 db="openstack_citest")
            cur = db.cursor()
            cur.execute("create database %s" % self.name)
            cur.execute(
                "grant all on %s.* to '%s'@'localhost' identified by '%s'" %
                (self.name, self.name, self.passwd))
            cur.execute("flush privileges")

        self.dburi = 'mysql+pymysql://%s:%s@localhost/%s' % (self.name,
                                                             self.passwd,
                                                             self.name)
        self.addDetail('dburi', testtools.content.text_content(self.dburi))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        lock = lockfile.LockFile('/tmp/nodepool-db-schema-lockfile')
        with lock:
            db = pymysql.connect(host="localhost",
                                 user="openstack_citest",
                                 passwd="openstack_citest",
                                 db="openstack_citest")
            cur = db.cursor()
            cur.execute("drop database %s" % self.name)
            cur.execute("drop user '%s'@'localhost'" % self.name)
            cur.execute("flush privileges")


class BuilderFixture(fixtures.Fixture):
    def __init__(self, configfile):
        super(BuilderFixture, self).__init__()
        self.configfile = configfile
        self.builder = None

    def setUp(self):
        super(BuilderFixture, self).setUp()
        self.builder = builder.NodePoolBuilder(self.configfile)
        self.builder.cleanup_interval = .5
        self.builder.build_interval = .1
        self.builder.upload_interval = .1
        self.builder.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.builder.stop()


class DBTestCase(BaseTestCase):
    def setUp(self):
        super(DBTestCase, self).setUp()
        self.log = logging.getLogger("tests")
        f = MySQLSchemaFixture()
        self.useFixture(f)
        self.dburi = f.dburi
        self.secure_conf = self._setup_secure()

        gearman_fixture = GearmanServerFixture()
        self.useFixture(gearman_fixture)
        self.gearman_server = gearman_fixture.gearman_server

        self.setupZK()

    def setup_config(self, filename, images_dir=None):
        if images_dir is None:
            images_dir = fixtures.TempDir()
            self.useFixture(images_dir)
        configfile = os.path.join(os.path.dirname(__file__),
                                  'fixtures', filename)
        (fd, path) = tempfile.mkstemp()
        with open(configfile) as conf_fd:
            config = conf_fd.read()
            os.write(fd, config.format(images_dir=images_dir.path,
                                       gearman_port=self.gearman_server.port,
                                       zookeeper_host=self.zookeeper_host,
                                       zookeeper_port=self.zookeeper_port,
                                       zookeeper_chroot=self.zookeeper_chroot))
        os.close(fd)
        self._config_images_dir = images_dir
        return path

    def replace_config(self, configfile, filename):
        new_configfile = self.setup_config(filename, self._config_images_dir)
        os.rename(new_configfile, configfile)

    def _setup_secure(self):
        # replace entries in secure.conf
        configfile = os.path.join(os.path.dirname(__file__),
                                  'fixtures', 'secure.conf')
        (fd, path) = tempfile.mkstemp()
        with open(configfile) as conf_fd:
            config = conf_fd.read()
            os.write(fd, config.format(dburi=self.dburi))
        os.close(fd)
        return path

    def wait_for_config(self, pool):
        for x in range(300):
            if pool.config is not None:
                return
            time.sleep(0.1)

    def waitForImage(self, provider_name, image_name, ignore_list=None):
        while True:
            self.wait_for_threads()
            image = self.zk.getMostRecentImageUpload(image_name, provider_name)
            if image:
                if ignore_list and image not in ignore_list:
                    break
                elif not ignore_list:
                    break
            time.sleep(1)
        self.wait_for_threads()
        return image

    def waitForImageDeletion(self, provider_name, image_name, match=None):
        while True:
            self.wait_for_threads()
            image = self.zk.getMostRecentImageUpload(image_name, provider_name)
            if not image or (match and image != match):
                break
            time.sleep(1)
        self.wait_for_threads()

    def waitForBuild(self, image_name, build_id):
        base = "-".join([image_name, build_id])
        while True:
            self.wait_for_threads()
            files = builder.DibImageFile.from_image_id(
                self._config_images_dir.path, base)
            if files:
                break
            time.sleep(1)

        while True:
            self.wait_for_threads()
            build = self.zk.getBuild(image_name, build_id)
            if build and build.state == zk.READY:
                break
            time.sleep(1)

        self.wait_for_threads()
        return build

    def waitForBuildDeletion(self, image_name, build_id):
        base = "-".join([image_name, build_id])
        while True:
            self.wait_for_threads()
            files = builder.DibImageFile.from_image_id(
                self._config_images_dir.path, base)
            if not files:
                break
            time.sleep(1)

        while True:
            self.wait_for_threads()
            build = self.zk.getBuild(image_name, build_id)
            if not build:
                break
            time.sleep(1)

        self.wait_for_threads()

    def waitForNodes(self, pool):
        self.wait_for_config(pool)
        allocation_history = allocation.AllocationHistory()
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                needed = pool.getNeededNodes(session, allocation_history)
                if not needed:
                    nodes = session.getNodes(state=nodedb.BUILDING)
                    if not nodes:
                        break
            time.sleep(1)
        self.wait_for_threads()

    def waitForJobs(self):
        # XXX:greghaynes - There is a very narrow race here where nodepool
        # is who actually updates the database so this may return before the
        # image rows are updated.
        client = GearmanClient()
        client.addServer('localhost', self.gearman_server.port)
        client.waitForServer()

        while client.get_queued_image_jobs() > 0:
            time.sleep(.2)
        client.shutdown()

    def useNodepool(self, *args, **kwargs):
        args = (self.secure_conf,) + args
        pool = nodepool.NodePool(*args, **kwargs)
        self.addCleanup(pool.stop)
        return pool

    def useWebApp(self, *args, **kwargs):
        app = webapp.WebApp(*args, **kwargs)
        self.addCleanup(app.stop)
        return app

    def _useBuilder(self, configfile):
        self.useFixture(BuilderFixture(configfile))

    def setupZK(self):
        f = ZookeeperServerFixture()
        self.useFixture(f)
        self.zookeeper_host = f.zookeeper_host
        self.zookeeper_port = f.zookeeper_port

        kz_fxtr = self.useFixture(ChrootedKazooFixture(
            self.zookeeper_host,
            self.zookeeper_port))
        self.zkclient = kz_fxtr.zkclient
        self.zk = zk.ZooKeeper(self.zkclient)
        self.zookeeper_chroot = kz_fxtr.chroot_path

    def printZKTree(self, node):
        def join(a, b):
            if a.endswith('/'):
                return a+b
            return a+'/'+b

        data, stat = self.zk.client.get(node)
        self.log.debug("Node: %s" % (node,))
        if data:
            self.log.debug(data)

        for child in self.zk.client.get_children(node):
            self.printZKTree(join(node, child))


class IntegrationTestCase(DBTestCase):
    def setUpFakes(self):
        pass

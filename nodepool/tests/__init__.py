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

import fixtures
import gear
import testresources
import testtools

from nodepool import allocation, fakeprovider, nodepool, nodedb

TRUE_VALUES = ('true', '1', 'yes')


class LoggingPopen(subprocess.Popen):
    pass


class FakeGearmanServer(gear.Server):
    def __init__(self):
        self.hold_jobs_in_queue = False
        super(FakeGearmanServer, self).__init__(0)

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
    def setUp(self):
        super(GearmanServerFixture, self).setUp()
        self.gearman_server = FakeGearmanServer()
        self.addCleanup(self.shutdownGearman)

    def shutdownGearman(self):
        self.gearman_server.shutdown()


class BaseTestCase(testtools.TestCase, testresources.ResourcedTestCase):
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
        db = pymysql.connect(host="localhost",
                             user="openstack_citest",
                             passwd="openstack_citest",
                             db="openstack_citest")
        cur = db.cursor()
        cur.execute("create database %s" % self.name)
        cur.execute("grant all on %s.* to '%s'@'localhost'" %
                    (self.name, self.name))
        cur.execute("flush privileges")

        self.dburi = 'mysql+pymysql://%s@localhost/%s' % (self.name, self.name)
        self.addDetail('dburi', testtools.content.text_content(self.dburi))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        db = pymysql.connect(host="localhost",
                             user="openstack_citest",
                             passwd="openstack_citest",
                             db="openstack_citest")
        cur = db.cursor()
        cur.execute("drop database %s" % self.name)


class DBTestCase(BaseTestCase):
    def setUp(self):
        super(DBTestCase, self).setUp()
        f = MySQLSchemaFixture()
        self.useFixture(f)
        self.dburi = f.dburi
        self.secure_conf = self._setup_secure()

        gearman_fixture = GearmanServerFixture()
        self.useFixture(gearman_fixture)
        self.gearman_server = gearman_fixture.gearman_server

    def setup_config(self, filename):
        images_dir = fixtures.TempDir()
        self.useFixture(images_dir)
        configfile = os.path.join(os.path.dirname(__file__),
                                  'fixtures', filename)
        config = open(configfile).read()
        (fd, path) = tempfile.mkstemp()
        os.write(fd, config.format(images_dir=images_dir.path,
                                   gearman_port=self.gearman_server.port))
        os.close(fd)
        return path

    def _setup_secure(self):
        # replace entries in secure.conf
        configfile = os.path.join(os.path.dirname(__file__),
                                  'fixtures', 'secure.conf')
        config = open(configfile).read()
        (fd, path) = tempfile.mkstemp()
        os.write(fd, config.format(dburi=self.dburi))
        os.close(fd)
        return path

    def wait_for_config(self, pool):
        for x in range(300):
            if pool.config is not None:
                return
            time.sleep(0.1)

    def waitForImage(self, pool, provider_name, image_name):
        self.wait_for_config(pool)
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                image = session.getCurrentSnapshotImage(provider_name,
                                                        image_name)
                if image:
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

    def useNodepool(self, *args, **kwargs):
        args = (self.secure_conf,) + args
        pool = nodepool.NodePool(*args, **kwargs)
        self.addCleanup(pool.stop)
        return pool


class IntegrationTestCase(DBTestCase):
    def setUpFakes(self):
        pass

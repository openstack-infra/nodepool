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
import MySQLdb
import os
import random
import string

import fixtures
import testresources
import testtools

TRUE_VALUES = ('true', '1', 'yes')


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


class AllocatorTestCase(BaseTestCase):
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


class RoundRobinTestCase(BaseTestCase):
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
        db = MySQLdb.connect(host="localhost",
                             user="openstack_citest",
                             passwd="openstack_citest",
                             db="openstack_citest")
        cur = db.cursor()
        cur.execute("create database %s" % self.name)
        cur.execute("grant all on %s.* to '%s'@'localhost'" %
                    (self.name, self.name))
        cur.execute("flush privileges")

        self.dburi = 'mysql://%s@localhost/%s' % (self.name, self.name)
        self.addDetail('dburi', testtools.content.text_content(self.dburi))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        db = MySQLdb.connect(host="localhost",
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

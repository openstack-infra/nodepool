#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
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

import uuid
import time
import threading
import novaclient
from jenkins import JenkinsException


class Dummy(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def delete(self):
        self.manager.delete(self)


class FakeList(object):
    def __init__(self, l):
        self._list = l

    def list(self):
        return self._list

    def find(self, name):
        for x in self._list:
            if x.name == name:
                return x

    def get(self, image=None):
        if image:
            id = image
        for x in self._list:
            if x.id == id:
                return x
        raise novaclient.exceptions.NotFound(404)

    def _finish(self, obj, delay, status):
        time.sleep(delay)
        obj.status = status

    def delete(self, *args, **kw):
        if 'image' in kw:
            self._list.remove(self.get(kw['image']))
        else:
            obj = args[0]
            if hasattr(obj, 'id'):
                self._list.remove(obj)
            else:
                self._list.remove(self.get(obj))

    def create(self, **kw):
        s = Dummy(id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=dict(public=[dict(version=4, addr='fake')]),
                  metadata={},
                  manager=self)
        self._list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.5, 'ACTIVE'))
        t.start()
        return s

    def create_image(self, server, image_name):
        x = self.api.images.create(name=image_name)
        return x.id


class FakeHTTPClient(object):
    def get(self, path):
        if path == '/extensions':
            return None, dict(extensions=dict())


class FakeClient(object):
    def __init__(self):
        self.flavors = FakeList([
            Dummy(id='f1', ram=8192, name='Fake Flavor'),
            Dummy(id='f2', ram=8192, name='Unreal Flavor'),
        ])
        self.images = FakeList([Dummy(id='i1', name='Fake Precise')])
        self.client = FakeHTTPClient()
        self.servers = FakeList([])
        self.servers.api = self

        self.client.user = 'fake'
        self.client.password = 'fake'
        self.client.projectid = 'fake'
        self.client.service_type = None
        self.client.service_name = None
        self.client.region_name = None


class FakeSSHClient(object):
    def ssh(self, description, cmd):
        return True

    def scp(self, src, dest):
        return True


class FakeJenkins(object):
    def __init__(self, user):
        self._nodes = {}
        self.quiet = False
        self.down = False
        if user == 'quiet':
            self.quiet = True
        if user == 'down':
            self.down = True

    def node_exists(self, name):
        return name in self._nodes

    def create_node(self, name, **kw):
        self._nodes[name] = kw

    def delete_node(self, name):
        del self._nodes[name]

    def get_info(self):
        if self.down:
            raise JenkinsException("Jenkins is down")
        d = {u'assignedLabels': [{}],
             u'description': None,
             u'jobs': [{u'color': u'red',
                        u'name': u'test-job',
                        u'url': u'https://jenkins.example.com/job/test-job/'}],
             u'mode': u'NORMAL',
             u'nodeDescription': u'the master Jenkins node',
             u'nodeName': u'',
             u'numExecutors': 1,
             u'overallLoad': {},
             u'primaryView': {u'name': u'Overview',
                              u'url': u'https://jenkins.example.com/'},
             u'quietingDown': self.quiet,
             u'slaveAgentPort': 8090,
             u'unlabeledLoad': {},
             u'useCrumbs': False,
             u'useSecurity': True,
             u'views': [
                 {u'name': u'test-view',
                  u'url': u'https://jenkins.example.com/view/test-view/'}]}
        return d

FAKE_CLIENT = FakeClient()

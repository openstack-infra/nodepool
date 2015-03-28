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

import StringIO
import novaclient
import requests.exceptions
import threading
import time
import uuid

from jenkins import JenkinsException


class Dummy(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def delete(self):
        self.manager.delete(self)

    def update(self, data):
        try:
            if self.should_fail:
                raise RuntimeError('This image has SHOULD_FAIL set to True.')
        except AttributeError:
            pass


fake_images_list = None


def get_fake_images_list():
    global fake_images_list
    if fake_images_list is None:
        fake_images_list = FakeList([Dummy(id='fake-image-id',
                                           status='READY',
                                           name='Fake Precise',
                                           metadata={})])
    return fake_images_list


BAD_CLIENT = None


def get_bad_client():
    global BAD_CLIENT
    if BAD_CLIENT is None:
        BAD_CLIENT = BadOpenstackCloud()
    return BAD_CLIENT


FAKE_CLIENT = None


def get_fake_client(**kwargs):
    global FAKE_CLIENT
    if FAKE_CLIENT is None:
        FAKE_CLIENT = FakeOpenStackCloud()
    return FAKE_CLIENT


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
        should_fail = kw.get('SHOULD_FAIL', '').lower() == 'true'
        s = Dummy(id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=dict(
                      public=[dict(version=4, addr='fake')],
                      private=[dict(version=4, addr='fake')]
                  ),
                  metadata={},
                  manager=self,
                  should_fail=should_fail)
        self._list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.1, 'ACTIVE'))
        t.start()
        return s

    def create_image(self, server, image_name, metadata):
        # XXX : validate metadata?
        x = self.api.images.create(name=image_name)
        return x.id


class FakeHTTPClient(object):
    def get(self, path):
        if path == '/extensions':
            return None, dict(extensions=dict())


class BadHTTPClient(object):
    '''Always raises a ProxyError'''
    def get(self, path):
        raise requests.exceptions.ProxyError


class FakeClient(object):
    def __init__(self, *args, **kwargs):
        self.flavors = FakeList([
            Dummy(id='f1', ram=8192, name='Fake Flavor'),
            Dummy(id='f2', ram=8192, name='Unreal Flavor'),
        ])
        self.images = get_fake_images_list()
        self.client = FakeHTTPClient()
        self.servers = FakeList([])
        self.servers.api = self


class BadClient(FakeClient):
    def __init__(self):
        super(BadClient, self).__init__()
        self.client = BadHTTPClient()


class BadOpenstackCloud(object):
    nova_client = BadClient()


class FakeGlanceClient(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.images = get_fake_images_list()


class FakeServiceCatalog(object):
    def url_for(self, **kwargs):
        return 'fake-url'


class FakeKeystoneClient(object):
    def __init__(self, **kwargs):
        self.service_catalog = FakeServiceCatalog()
        self.auth_token = 'fake-auth-token'


class FakeOpenStackCloud(object):
    nova_client = FakeClient()
    glance_client = FakeGlanceClient()


class FakeFile(StringIO.StringIO):
    def __init__(self, path):
        StringIO.StringIO.__init__(self)
        self.__path = path

    def close(self):
        print "Wrote to %s:" % self.__path
        print self.getvalue()
        StringIO.StringIO.close(self)


class FakeSFTPClient(object):
    def open(self, path, mode):
        return FakeFile(path)

    def close(self):
        pass


class FakeSSHClient(object):
    def __init__(self):
        self.client = self

    def ssh(self, description, cmd, output=False):
        return True

    def scp(self, src, dest):
        return True

    def open_sftp(self):
        return FakeSFTPClient()


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

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
import re

import myjenkins
import fakeprovider
from task_manager import Task, TaskManager


class CreateNodeTask(Task):
    def main(self, jenkins):
        if 'credentials_id' in self.args:
            launcher_params = {'port': 22,
                               'credentialsId': self.args['credentials_id'],
                               'host': self.args['host']}
        else:
            launcher_params = {'port': 22,
                               'username': self.args['username'],
                               'privatekey': self.args['private_key'],
                               'host': self.args['host']}
        args = dict(
            name=self.args['name'],
            numExecutors=self.args['executors'],
            nodeDescription=self.args['description'],
            remoteFS=self.args['root'],
            exclusive=True,
            launcher='hudson.plugins.sshslaves.SSHLauncher',
            launcher_params=launcher_params)
        if self.args['labels']:
            args['labels'] = self.args['labels']
        try:
            jenkins.create_node(**args)
        except myjenkins.JenkinsException as e:
            if 'already exists' in str(e):
                pass
            else:
                raise


class NodeExistsTask(Task):
    def main(self, jenkins):
        return jenkins.node_exists(self.args['name'])


class DeleteNodeTask(Task):
    def main(self, jenkins):
        return jenkins.delete_node(self.args['name'])


class GetNodeConfigTask(Task):
    def main(self, jenkins):
        return jenkins.get_node_config(self.args['name'])


class SetNodeConfigTask(Task):
    def main(self, jenkins):
        jenkins.reconfig_node(self.args['name'], self.args['config'])


class StartBuildTask(Task):
    def main(self, jenkins):
        jenkins.build_job(self.args['name'],
                          parameters=self.args['params'])


class GetInfoTask(Task):
    def main(self, jenkins):
        return jenkins.get_info()


class JenkinsManager(TaskManager):
    log = logging.getLogger("nodepool.JenkinsManager")

    def __init__(self, target):
        super(JenkinsManager, self).__init__(None, target.name, target.rate)
        self.target = target
        self._client = self._getClient()

    def _getClient(self):
        if self.target.jenkins_apikey == 'fake':
            return fakeprovider.FakeJenkins(self.target.jenkins_user)
        return myjenkins.Jenkins(self.target.jenkins_url,
                                 self.target.jenkins_user,
                                 self.target.jenkins_apikey)

    def createNode(self, name, host, description, executors, root, labels=[],
                   credentials_id=None, username=None, private_key=None):
        args = dict(name=name, host=host, description=description,
                    labels=labels, executors=executors, root=root)
        if credentials_id:
            args['credentials_id'] = credentials_id
        else:
            args['username'] = username
            args['private_key'] = private_key
        return self.submitTask(CreateNodeTask(**args))

    def nodeExists(self, name):
        return self.submitTask(NodeExistsTask(name=name))

    def deleteNode(self, name):
        return self.submitTask(DeleteNodeTask(name=name))

    LABEL_RE = re.compile(r'<label>(.*)</label>')

    def relabelNode(self, name, labels):
        config = self.submitTask(GetNodeConfigTask(name=name))
        old = None
        m = self.LABEL_RE.search(config)
        if m:
            old = m.group(1)
        config = self.LABEL_RE.sub('<label>%s</label>' % ' '.join(labels),
                                   config)
        self.submitTask(SetNodeConfigTask(name=name, config=config))
        return old

    def startBuild(self, name, params):
        self.submitTask(StartBuildTask(name=name, params=params))

    def getInfo(self):
        return self._client.get_info()

#!/usr/bin/env python
# Copyright 2011-2013 OpenStack Foundation
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

import jenkins
import json

import six.moves.urllib.parse as urlparse
import six.moves.urllib.request as urlrequest

from jenkins import JenkinsException, NODE_TYPE, CREATE_NODE

TOGGLE_OFFLINE = '/computer/%(name)s/toggleOffline?offlineMessage=%(msg)s'
CONFIG_NODE = '/computer/%(name)s/config.xml'


class Jenkins(jenkins.Jenkins):
    def disable_node(self, name, msg=''):
        '''
        Disable a node

        @param name: Jenkins node name
        @type  name: str
        @param msg: Offline message
        @type  msg: str
        '''
        info = self.get_node_info(name)
        if info['offline']:
            return
        self.jenkins_open(
            urlrequest.Request(self.server + TOGGLE_OFFLINE % locals()))

    def enable_node(self, name):
        '''
        Enable a node

        @param name: Jenkins node name
        @type  name: str
        '''
        info = self.get_node_info(name)
        if not info['offline']:
            return
        msg = ''
        self.jenkins_open(
            urlrequest.Request(self.server + TOGGLE_OFFLINE % locals()))

    def get_node_config(self, name):
        '''
        Get the configuration for a node.

        :param name: Jenkins node name, ``str``
        '''
        get_config_url = self.server + CONFIG_NODE % locals()
        return self.jenkins_open(urlrequest.Request(get_config_url))

    def reconfig_node(self, name, config_xml):
        '''
        Change the configuration for an existing node.

        :param name: Jenkins node name, ``str``
        :param config_xml: New XML configuration, ``str``
        '''
        headers = {'Content-Type': 'text/xml'}
        reconfig_url = self.server + CONFIG_NODE % locals()
        self.jenkins_open(
            urlrequest.Request(reconfig_url, config_xml, headers))

    def create_node(self, name, numExecutors=2, nodeDescription=None,
                    remoteFS='/var/lib/jenkins', labels=None, exclusive=False,
                    launcher='hudson.slaves.JNLPLauncher', launcher_params={}):
        '''
        @param name: name of node to create
        @type  name: str
        @param numExecutors: number of executors for node
        @type  numExecutors: int
        @param nodeDescription: Description of node
        @type  nodeDescription: str
        @param remoteFS: Remote filesystem location to use
        @type  remoteFS: str
        @param labels: Labels to associate with node
        @type  labels: str
        @param exclusive: Use this node for tied jobs only
        @type  exclusive: boolean
        @param launcher: The launch method for the slave
        @type  launcher: str
        @param launcher_params: Additional parameters for the launcher
        @type  launcher_params: dict
        '''
        if self.node_exists(name):
            raise JenkinsException('node[%s] already exists' % (name))

        mode = 'NORMAL'
        if exclusive:
            mode = 'EXCLUSIVE'

        #hudson.plugins.sshslaves.SSHLauncher
        #hudson.slaves.CommandLauncher
        #hudson.os.windows.ManagedWindowsServiceLauncher
        launcher_params['stapler-class'] = launcher

        inner_params = {
            'name': name,
            'nodeDescription': nodeDescription,
            'numExecutors': numExecutors,
            'remoteFS': remoteFS,
            'labelString': labels,
            'mode': mode,
            'type': NODE_TYPE,
            'retentionStrategy': {
                'stapler-class': 'hudson.slaves.RetentionStrategy$Always'},
            'nodeProperties': {'stapler-class-bag': 'true'},
            'launcher': launcher_params
        }

        params = {
            'name': name,
            'type': NODE_TYPE,
            'json': json.dumps(inner_params)
        }

        self.jenkins_open(urlrequest.Request(
            self.server + CREATE_NODE % urlparse.urlencode(params)))

        if not self.node_exists(name):
            raise JenkinsException('create[%s] failed' % (name))

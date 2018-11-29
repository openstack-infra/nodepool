#!/usr/bin/env python
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

"""
Helper to create a statsd client from environment variables
"""

import os
import logging
import statsd

from nodepool import zk

log = logging.getLogger("nodepool.stats")


def get_client():
    """Return a statsd client object setup from environment variables; or
    None if they are not set
    """

    # note we're just being careful to let the default values fall
    # through to StatsClient()
    statsd_args = {}
    if os.getenv('STATSD_HOST', None):
        statsd_args['host'] = os.environ['STATSD_HOST']
    if os.getenv('STATSD_PORT', None):
        statsd_args['port'] = os.environ['STATSD_PORT']
    if statsd_args:
        return statsd.StatsClient(**statsd_args)
    else:
        return None


class StatsReporter(object):
    '''
    Class adding statsd reporting functionality.
    '''
    def __init__(self):
        super(StatsReporter, self).__init__()
        self._statsd = get_client()

    def recordLaunchStats(self, subkey, dt):
        '''
        Record node launch statistics.

        :param str subkey: statsd key
        :param int dt: Time delta in milliseconds
        '''
        if not self._statsd:
            return

        keys = [
            'nodepool.launch.provider.%s.%s' % (
                self.provider_config.name, subkey),
            'nodepool.launch.%s' % (subkey,),
        ]

        if self.node.az:
            keys.append('nodepool.launch.provider.%s.%s.%s' %
                        (self.provider_config.name, self.node.az, subkey))

        if self.handler.request.requestor:
            # Replace '.' which is a graphite hierarchy, and ':' which is
            # a statsd delimeter.
            requestor = self.handler.request.requestor.replace('.', '_')
            requestor = requestor.replace(':', '_')
            keys.append('nodepool.launch.requestor.%s.%s' %
                        (requestor, subkey))

        pipeline = self._statsd.pipeline()
        for key in keys:
            pipeline.timing(key, dt)
            pipeline.incr(key)
        pipeline.send()

    def updateNodeStats(self, zk_conn):
        '''
        Refresh statistics for all known nodes.

        :param ZooKeeper zk_conn: A ZooKeeper connection object.
        '''
        if not self._statsd:
            return

        states = {}

        launchers = zk_conn.getRegisteredLaunchers()
        labels = set()
        for launcher in launchers:
            labels.update(launcher.supported_labels)
        providers = set()
        for launcher in launchers:
            providers.add(launcher.provider_name)

        # Initialize things we know about to zero
        for state in zk.Node.VALID_STATES:
            key = 'nodepool.nodes.%s' % state
            states[key] = 0
            for provider in providers:
                key = 'nodepool.provider.%s.nodes.%s' % (provider, state)
                states[key] = 0

        # Initialize label stats to 0
        for label in labels:
            for state in zk.Node.VALID_STATES:
                key = 'nodepool.label.%s.nodes.%s' % (label, state)
                states[key] = 0

        for node in zk_conn.nodeIterator():
            # nodepool.nodes.STATE
            key = 'nodepool.nodes.%s' % node.state
            states[key] += 1

            # nodepool.label.LABEL.nodes.STATE
            # nodes can have several labels
            for label in node.type:
                key = 'nodepool.label.%s.nodes.%s' % (label, node.state)
                # It's possible we could see node types that aren't in our
                # config
                if key in states:
                    states[key] += 1
                else:
                    states[key] = 1

            # nodepool.provider.PROVIDER.nodes.STATE
            key = 'nodepool.provider.%s.nodes.%s' % (node.provider, node.state)
            # It's possible we could see providers that aren't in our config
            if key in states:
                states[key] += 1
            else:
                states[key] = 1

        pipeline = self._statsd.pipeline()
        for key, count in states.items():
            pipeline.gauge(key, count)

        pipeline.send()

    def updateProviderLimits(self, provider):
        if not self._statsd:
            return

        pipeline = self._statsd.pipeline()

        # nodepool.provider.PROVIDER.max_servers
        key = 'nodepool.provider.%s.max_servers' % provider.name
        max_servers = sum([p.max_servers for p in provider.pools.values()
                           if p.max_servers])
        pipeline.gauge(key, max_servers)

        pipeline.send()

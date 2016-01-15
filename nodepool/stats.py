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

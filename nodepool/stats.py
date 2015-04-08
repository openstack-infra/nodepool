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
Import and set `statsd` if STATSD_HOST is present in the
environment, else set it to None.  This mirrors the behaviour of old
releases of upstream statsd and avoids us having to change anything
else.
"""

import os
import logging

log = logging.getLogger("nodepool.stats")

if os.getenv('STATSD_HOST', None):
    from statsd.defaults.env import statsd
    log.info("Statsd reporting to %s:%s" %
             (os.getenv('STATSD_HOST'),
              os.getenv('STATSD_PORT', '8125')))
else:
    log.info("Statsd reporting disabled")
    statsd = None

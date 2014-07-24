# Copyright (C) 2014 OpenStack Foundation
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
# See the License for the specific language governing permissions and
# limitations under the License.

import testscenarios

from nodepool import tests
from nodepool import allocation


class OneLabel(tests.AllocatorTestCase):
    """The simplest case: one each of providers, labels, and
    targets.

    Result AGT is:
      * label1 from provider1
    """

    scenarios = [
        ('one_node',
         dict(provider1=10, label1=1, results=[1])),
        ('two_nodes',
         dict(provider1=10, label1=2, results=[2])),
        ]

    def setUp(self):
        super(OneLabel, self).setUp()
        ap1 = allocation.AllocationProvider('provider1', self.provider1)
        at1 = allocation.AllocationTarget('target1')
        ar1 = allocation.AllocationRequest('label1', self.label1)
        ar1.addTarget(at1, 0)
        self.agt.append(ar1.addProvider(ap1, at1, 0)[1])
        ap1.makeGrants()


class TwoLabels(tests.AllocatorTestCase):
    """Two labels from one provider.

    Result AGTs are:
      * label1 from provider1
      * label1 from provider2
    """

    scenarios = [
        ('one_node',
         dict(provider1=10, label1=1, label2=1, results=[1, 1])),
        ('two_nodes',
         dict(provider1=10, label1=2, label2=2, results=[2, 2])),
        ]

    def setUp(self):
        super(TwoLabels, self).setUp()
        ap1 = allocation.AllocationProvider('provider1', self.provider1)
        at1 = allocation.AllocationTarget('target1')
        ar1 = allocation.AllocationRequest('label1', self.label1)
        ar2 = allocation.AllocationRequest('label2', self.label2)
        ar1.addTarget(at1, 0)
        ar2.addTarget(at1, 0)
        self.agt.append(ar1.addProvider(ap1, at1, 0)[1])
        self.agt.append(ar2.addProvider(ap1, at1, 0)[1])
        ap1.makeGrants()


class TwoProvidersTwoLabels(tests.AllocatorTestCase):
    """Two labels, each of which is supplied by both providers.

    Result AGTs are:
      * label1 from provider1
      * label2 from provider1
      * label1 from provider2
      * label2 from provider2
    """

    scenarios = [
        ('one_node',
         dict(provider1=10, provider2=10, label1=1, label2=1,
              results=[1, 1, 0, 0])),
        ('two_nodes',
         dict(provider1=10, provider2=10, label1=2, label2=2,
              results=[1, 1, 1, 1])),
        ('three_nodes',
         dict(provider1=10, provider2=10, label1=3, label2=3,
              results=[2, 2, 1, 1])),
        ('four_nodes',
         dict(provider1=10, provider2=10, label1=4, label2=4,
              results=[2, 2, 2, 2])),
        ('four_nodes_at_quota',
         dict(provider1=4, provider2=4, label1=4, label2=4,
              results=[2, 2, 2, 2])),
        ('four_nodes_over_quota',
         dict(provider1=2, provider2=2, label1=4, label2=4,
              results=[1, 1, 1, 1])),
        ('negative_provider',
         dict(provider1=-5, provider2=20, label1=5, label2=5,
              results=[0, 0, 5, 5])),
        ]

    def setUp(self):
        super(TwoProvidersTwoLabels, self).setUp()
        ap1 = allocation.AllocationProvider('provider1', self.provider1)
        ap2 = allocation.AllocationProvider('provider2', self.provider2)
        at1 = allocation.AllocationTarget('target1')
        ar1 = allocation.AllocationRequest('label1', self.label1)
        ar2 = allocation.AllocationRequest('label2', self.label2)
        ar1.addTarget(at1, 0)
        ar2.addTarget(at1, 0)
        self.agt.append(ar1.addProvider(ap1, at1, 0)[1])
        self.agt.append(ar2.addProvider(ap1, at1, 0)[1])
        self.agt.append(ar1.addProvider(ap2, at1, 0)[1])
        self.agt.append(ar2.addProvider(ap2, at1, 0)[1])
        ap1.makeGrants()
        ap2.makeGrants()


class TwoProvidersTwoLabelsOneShared(tests.AllocatorTestCase):
    """One label is served by both providers, the other can only come
    from one.  This tests that the allocator uses the diverse provider
    to supply the label that can come from either while reserving
    nodes from the more restricted provider for the label that can
    only be supplied by it.

    label1 is supplied by provider1 and provider2.
    label2 is supplied only by provider2.

    Result AGTs are:
      * label1 from provider1
      * label2 from provider1
      * label2 from provider2
    """

    scenarios = [
        ('one_node',
         dict(provider1=10, provider2=10, label1=1, label2=1,
              results=[1, 1, 0])),
        ('two_nodes',
         dict(provider1=10, provider2=10, label1=2, label2=2,
              results=[2, 1, 1])),
        ('three_nodes',
         dict(provider1=10, provider2=10, label1=3, label2=3,
              results=[3, 2, 1])),
        ('four_nodes',
         dict(provider1=10, provider2=10, label1=4, label2=4,
              results=[4, 2, 2])),
        ('four_nodes_at_quota',
         dict(provider1=4, provider2=4, label1=4, label2=4,
              results=[4, 0, 4])),
        ('four_nodes_over_quota',
         dict(provider1=2, provider2=2, label1=4, label2=4,
              results=[2, 0, 2])),
        ]

    def setUp(self):
        super(TwoProvidersTwoLabelsOneShared, self).setUp()
        ap1 = allocation.AllocationProvider('provider1', self.provider1)
        ap2 = allocation.AllocationProvider('provider2', self.provider2)
        at1 = allocation.AllocationTarget('target1')
        ar1 = allocation.AllocationRequest('label1', self.label1)
        ar2 = allocation.AllocationRequest('label2', self.label2)
        ar1.addTarget(at1, 0)
        ar2.addTarget(at1, 0)
        self.agt.append(ar1.addProvider(ap1, at1, 0)[1])
        self.agt.append(ar2.addProvider(ap1, at1, 0)[1])
        self.agt.append(ar2.addProvider(ap2, at1, 0)[1])
        ap1.makeGrants()
        ap2.makeGrants()


def load_tests(loader, in_tests, pattern):
    return testscenarios.load_tests_apply_scenarios(loader, in_tests, pattern)

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


class OneLabel(tests.AllocatorTestCase, tests.BaseTestCase):
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


class TwoLabels(tests.AllocatorTestCase, tests.BaseTestCase):
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


class TwoProvidersTwoLabels(tests.AllocatorTestCase, tests.BaseTestCase):
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


class TwoProvidersTwoLabelsOneShared(tests.AllocatorTestCase,
                                     tests.BaseTestCase):
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


class RoundRobinAllocation(tests.RoundRobinTestCase, tests.BaseTestCase):
    """Test the round-robin behaviour of the AllocationHistory object to
    ensure fairness of distribution

    """

    scenarios = [
        # * one_to_one
        #
        # test that with only one node available we cycle through the
        # available labels.
        #
        # There's a slight trick with the ordering here; makeGrants()
        # algorithm allocates proportionally from the available nodes
        # (i.e. if there's allocations for 100 and 50, then the first
        # gets twice as many of the available nodes than the second).
        # The algorithm is
        #
        #  1) add up all your peer requests
        #  2) calculate your ratio = (your_request / all_peers)
        #  3) multiples that ratio by the available nodes
        #  4) take the floor() (you can only allocate a whole node)
        #
        # So we've got 8 total requests, each requesting one node:
        #
        #  label1 = 1/7 other requests = 0.142 * 1 available node = 0
        #  label2 = 1/6 other requests = 0.166 * 1 available node = 0
        #  label3 = 1/4 other requests = 0.25  * 1 available node = 0
        #  ...
        #  label7 = 1/1 other requests = 1 * 1 available node = 1
        #
        # ergo label7 is the first to be granted its request.  Thus we
        # start the round-robin from there
        ('one_to_one',
         dict(provider1=1, provider2=0,
              label1=1, label2=1, label3=1, label4=1,
              label5=1, label6=1, label7=1, label8=1,
              results=['label7',
                       'label1',
                       'label2',
                       'label3',
                       'label4',
                       'label5',
                       'label6',
                       'label8',
                       'label7',
                       'label1',
                       'label2'])),

        # * at_quota
        #
        # Test that when at quota, every node gets allocated on every
        # round; i.e. nobody ever misses out.  odds go to ap1, even to
        # ap2
        ('at_quota',
         dict(provider1=4, provider2=4,
              label1=1, label2=1, label3=1, label4=1,
              label5=1, label6=1, label7=1, label8=1,
              results=[
                  'label1', 'label3', 'label5', 'label7',
                  'label2', 'label4', 'label6', 'label8'] * 11
              )),

        # * big_fish_little_pond
        #
        # In this test we have one label that far outweighs the other.
        # From the description of the ratio allocation above, it can
        # swamp the allocation pool and not allow other nodes to come
        # online.
        #
        # Here with two nodes, we check that one node is dedicated to
        # the larger label request, but the second node cycles through
        # the smaller requests.
        ('big_fish_little_pond',
         dict(provider1=1, provider2=1,
              label1=100, label2=1, label3=1, label4=1,
              label5=1, label6=1, label7=1, label8=1,
              #        provider1 provider2
              results=['label1', 'label1',  # round 1
                       'label1', 'label2',  # round 2
                       'label1', 'label3',  # ...
                       'label1', 'label4',
                       'label1', 'label5',
                       'label1', 'label6',
                       'label1', 'label7',
                       'label1', 'label8',
                       'label1', 'label2',
                       'label1', 'label3',
                       'label1', 'label4'])),
    ]

    def setUp(self):
        super(RoundRobinAllocation, self).setUp()

        ah = allocation.AllocationHistory()

        def do_it():
            ap1 = allocation.AllocationProvider('provider1', self.provider1)
            ap2 = allocation.AllocationProvider('provider2', self.provider2)

            at1 = allocation.AllocationTarget('target1')

            ars = []
            ars.append(allocation.AllocationRequest('label1', self.label1, ah))
            ars.append(allocation.AllocationRequest('label2', self.label2, ah))
            ars.append(allocation.AllocationRequest('label3', self.label3, ah))
            ars.append(allocation.AllocationRequest('label4', self.label4, ah))
            ars.append(allocation.AllocationRequest('label5', self.label5, ah))
            ars.append(allocation.AllocationRequest('label6', self.label6, ah))
            ars.append(allocation.AllocationRequest('label7', self.label7, ah))
            ars.append(allocation.AllocationRequest('label8', self.label8, ah))

            # each request to one target, and can be satisfied by both
            # providers
            for ar in ars:
                ar.addTarget(at1, 0)
                ar.addProvider(ap1, at1, 0)
                ar.addProvider(ap2, at1, 0)

            ap1.makeGrants()
            for g in ap1.grants:
                self.allocations.append(g.request.name)
            ap2.makeGrants()
            for g in ap2.grants:
                self.allocations.append(g.request.name)

            ah.grantsDone()

        # run the test several times to make sure we bounce around
        # enough
        for i in range(0, 11):
            do_it()


class RoundRobinFixedProvider(tests.RoundRobinTestCase, tests.BaseTestCase):
    """Test that round-robin behaviour exists when we have a more complex
    situation where some nodes can only be provided by some providers

     * label1 is only able to be allocated from provider1
     * label8 is only able to be allocated from provider2
    """

    scenarios = [
        # * fixed_even
        #
        # What we see below is an edge case:
        #
        # Below, label1 always gets chosen because for provider1.
        # This is because label1 is requesting 1.0 nodes (it can only
        # run on provider1) and all the other labels are requesting
        # only 0.5 of a node (they can run on either and no
        # allocations have been made yet).  We do actually grant in a
        # round-robin fashion, but int(0.5) == 0 so no node gets
        # allocated.  We fall back to the ratio calculation and label1
        # wins.
        #
        # However, after provider1.makeGrants(), the other labels
        # increase their request on the remaning provider2 to their
        # full 1.0 nodes.  Now the "fight" starts and we allocate in
        # the round-robin fashion.
        ('fixed_even',
         dict(provider1=1, provider2=1,
              label1=1, label2=1, label3=1, label4=1,
              label5=1, label6=1, label7=1, label8=1,
              #        provider1 provider2
              results=['label1', 'label6',  # round 1
                       'label1', 'label8',  # round 2
                       'label1', 'label2',  # ...
                       'label1', 'label3',
                       'label1', 'label4',
                       'label1', 'label5',
                       'label1', 'label7',
                       'label1', 'label6',
                       'label1', 'label8',
                       'label1', 'label2',
                       'label1', 'label3'])),

        # * over_subscribed
        #
        # In contrast to above, any grant made will be satisfied.  We
        # see that the fixed node label1 and label8 do not get as full
        # a share as the non-fixed nodes -- but they do round-robin
        # with the other requests.  Fixing this is left as an exercise
        # for the reader :)
        ('over_subscribed',
         dict(provider1=1, provider2=1,
              label1=20, label2=20, label3=20, label4=20,
              label5=20, label6=20, label7=20, label8=20,
              results=['label1', 'label6',
                       'label2', 'label8',
                       'label3', 'label3',
                       'label4', 'label4',
                       'label5', 'label5',
                       'label7', 'label7',
                       'label1', 'label6',
                       'label2', 'label8',
                       'label3', 'label3',
                       'label4', 'label4',
                       'label5', 'label5'])),

        # * even
        #
        #  When there's enough nodes to go around, we expect everyone
        #  to be fully satisifed with label1 on provider1 and label8
        #  on provider2 as required
        ('even',
         dict(provider1=4, provider2=4,
              label1=1, label2=1, label3=1, label4=1,
              label5=1, label6=1, label7=1, label8=1,
              results=[
                  'label1', 'label2', 'label4', 'label6',
                  'label8', 'label3', 'label5', 'label7'] * 11))]

    def setUp(self):
        super(RoundRobinFixedProvider, self).setUp()

        ah = allocation.AllocationHistory()

        def do_it():
            ap1 = allocation.AllocationProvider('provider1', self.provider1)
            ap2 = allocation.AllocationProvider('provider2', self.provider2)

            at1 = allocation.AllocationTarget('target1')

            ars = []
            ars.append(allocation.AllocationRequest('label1', self.label1, ah))
            ars.append(allocation.AllocationRequest('label2', self.label2, ah))
            ars.append(allocation.AllocationRequest('label3', self.label3, ah))
            ars.append(allocation.AllocationRequest('label4', self.label4, ah))
            ars.append(allocation.AllocationRequest('label5', self.label5, ah))
            ars.append(allocation.AllocationRequest('label6', self.label6, ah))
            ars.append(allocation.AllocationRequest('label7', self.label7, ah))
            ars.append(allocation.AllocationRequest('label8', self.label8, ah))

            # first ar can only go to provider1, the last only to
            # provider2
            ars[0].addTarget(at1, 0)
            ars[0].addProvider(ap1, at1, 0)
            ars[-1].addTarget(at1, 0)
            ars[-1].addProvider(ap2, at1, 0)

            # the rest can go anywhere
            for ar in ars[1:-1]:
                ar.addTarget(at1, 0)
                ar.addProvider(ap1, at1, 0)
                ar.addProvider(ap2, at1, 0)

            ap1.makeGrants()
            for g in ap1.grants:
                self.allocations.append(g.request.name)

            ap2.makeGrants()
            for g in ap2.grants:
                self.allocations.append(g.request.name)

            ah.grantsDone()

        # run the test several times to make sure we bounce around
        # enough
        for i in range(0, 11):
            do_it()


def load_tests(loader, in_tests, pattern):
    return testscenarios.load_tests_apply_scenarios(loader, in_tests, pattern)

#!/usr/bin/env python

# Copyright (C) 2013 OpenStack Foundation
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

"""
This module holds classes that represent concepts in nodepool's
allocation algorithm.

The algorithm is:

  Setup:

  * Establish the node providers with their current available
    capacity.
  * Establish requests that are to be made of each provider for a
    certain label.
  * Indicate which providers can supply nodes of that label.
  * Indicate to which targets nodes of a certain label from a certain
    provider may be distributed (and the weight that should be
    given to each target when distributing).

  Run:

  * For each label, set the requested number of nodes from each
    provider to be proportional to that providers overall capacity.

  * Define the 'priority' of a request as the number of requests for
    the same label from other providers.

  * For each provider, sort the requests by the priority.  This puts
    requests that can be serviced by the fewest providers first.

  * Grant each such request in proportion to that requests portion of
    the total amount requested by requests of the same priority.

  * The nodes allocated by a grant are then distributed to the targets
    which are associated with the provider and label, in proportion to
    that target's portion of the sum of the weights of each target for
    that label.
"""

import functools

# History allocation tracking

#  The goal of the history allocation tracking is to ensure forward
#  progress by not starving any particular label when in over-quota
#  situations.  For example, if you have two labels, say 'fedora' and
#  'ubuntu', and 'ubuntu' is requesting many more nodes than 'fedora',
#  it is quite possible that 'fedora' never gets any allocations.  If
#  'fedora' is required for a gate-check job, older changes may wait
#  in Zuul's pipelines longer than expected while jobs for newer
#  changes continue to receive 'ubuntu' nodes and overall merge
#  throughput decreases during such contention.
#
#  We track the history of allocations by label.  A persistent
#  AllocationHistory object should be kept and passed along with each
#  AllocationRequest, which records its initial request in the history
#  via recordRequest().
#
#  When a sub-allocation gets a grant, it records this via a call to
#  AllocationHistory.recordGrant().  All the sub-allocations
#  contribute to tracking the total grants for the parent
#  AllocationRequest.
#
#  When finished requesting grants from all providers,
#  AllocationHistory.grantsDone() should be called to store the
#  allocation state in the history.
#
#  This history is used AllocationProvider.makeGrants() to prioritize
#  requests that have not been granted in prior iterations.
#  AllocationHistory.getWaitTime will return how many iterations
#  each label has been waiting for an allocation.


class AllocationHistory(object):
    '''A history of allocation requests and grants'''

    def __init__(self, history=100):
        # current allocations for this iteration
        # keeps elements of type
        #   label -> (request, granted)
        self.current_allocations = {}

        self.history = history
        # list of up to <history> previous current_allocation
        # dictionaries
        self.past_allocations = []

    def recordRequest(self, label, amount):
        try:
            a = self.current_allocations[label]
            a['requested'] += amount
        except KeyError:
            self.current_allocations[label] = dict(requested=amount,
                                                   allocated=0)

    def recordGrant(self, label, amount):
        try:
            a = self.current_allocations[label]
            a['allocated'] += amount
        except KeyError:
            # granted but not requested?  shouldn't happen
            raise

    def grantsDone(self):
        # save this round of allocations/grants up to our history
        self.past_allocations.insert(0, self.current_allocations)
        self.past_allocations = self.past_allocations[:self.history]
        self.current_allocations = {}

    def getWaitTime(self, label):
        # go through the history of allocations and calculate how many
        # previous iterations this label has received none of its
        # requested allocations.
        wait = 0

        # We don't look at the current_alloctions here; only
        # historical.  With multiple providers, possibly the first
        # provider has given nodes to the waiting label (which would
        # be recorded in current_allocations), and a second provider
        # should fall back to using the usual ratio-based mechanism?
        for i, a in enumerate(self.past_allocations):
            if (label in a) and (a[label]['allocated'] == 0):
                wait = i + 1
                continue

            # only interested in consecutive failures to allocate.
            break

        return wait


class AllocationProvider(object):
    """A node provider and its capacity."""
    def __init__(self, name, available):
        self.name = name
        # if this is negative, many of the calcuations turn around and
        # we start handing out nodes that don't exist.
        self.available = available if available >= 0 else 0
        self.sub_requests = []
        self.grants = []

    def __repr__(self):
        return '<AllocationProvider %s>' % self.name

    def makeGrants(self):
        # build a list of (request,wait-time) tuples
        all_reqs = [(x, x.getWaitTime()) for x in self.sub_requests]

        # reqs with no wait time get processed via ratio mechanism
        reqs = [x[0] for x in all_reqs if x[1] == 0]

        # we prioritize whoever has been waiting the longest and give
        # them whatever is available.  If we run out, put them back in
        # the ratio queue
        waiters = [x for x in all_reqs if x[1] != 0]
        waiters.sort(key=lambda x: x[1], reverse=True)

        for w in waiters:
            w = w[0]
            if self.available > 0:
                w.grant(min(int(w.amount), self.available))
            else:
                reqs.append(w)

        # Sort the remaining requests by priority so we fill the most
        # specific requests first (e.g., if this provider is the only
        # one that can supply foo nodes, then it should focus on
        # supplying them and leave bar nodes to other providers).
        reqs.sort(lambda a, b: cmp(a.getPriority(), b.getPriority()))

        for req in reqs:
            total_requested = 0.0
            # Within a specific priority, limit the number of
            # available nodes to a value proportionate to the request.
            reqs_at_this_level = [r for r in reqs
                                  if r.getPriority() == req.getPriority()]
            for r in reqs_at_this_level:
                total_requested += r.amount
            if total_requested:
                ratio = float(req.amount) / total_requested
            else:
                ratio = 0.0

            grant = int(round(req.amount))
            grant = min(grant, int(round(self.available * ratio)))
            # This adjusts our availability as well as the values of
            # other requests, so values will be correct the next time
            # through the loop.
            req.grant(grant)


class AllocationRequest(object):
    """A request for a number of labels."""

    def __init__(self, name, amount, history=None):
        self.name = name
        self.amount = float(amount)
        # Sub-requests of individual providers that make up this
        # request.  AllocationProvider -> AllocationSubRequest
        self.sub_requests = {}
        # Targets to which nodes from this request may be assigned.
        # AllocationTarget -> AllocationRequestTarget
        self.request_targets = {}

        if history is not None:
            self.history = history
        else:
            self.history = AllocationHistory()

        self.history.recordRequest(name, amount)

        # subrequests use these
        self.recordGrant = functools.partial(self.history.recordGrant, name)
        self.getWaitTime = functools.partial(self.history.getWaitTime, name)

    def __repr__(self):
        return '<AllocationRequest for %s of %s>' % (self.amount, self.name)

    def addTarget(self, target, current):
        art = AllocationRequestTarget(self, target, current)
        self.request_targets[target] = art

    def addProvider(self, provider, target, subnodes):
        # Handle being called multiple times with different targets.
        s = self.sub_requests.get(provider)
        if not s:
            s = AllocationSubRequest(self, provider, subnodes)
        agt = s.addTarget(self.request_targets[target])
        self.sub_requests[provider] = s
        if s not in provider.sub_requests:
            provider.sub_requests.append(s)
        self.makeRequests()
        return s, agt

    def makeRequests(self):
        # (Re-)distribute this request across all of its providers.
        total_available = 0.0
        for sub_request in self.sub_requests.values():
            total_available += sub_request.provider.available
        for sub_request in self.sub_requests.values():
            if total_available:
                ratio = float(sub_request.provider.available) / total_available
            else:
                ratio = 0.0
            sub_request.setAmount(ratio * self.amount)


class AllocationSubRequest(object):
    """A request for a number of images from a specific provider."""
    def __init__(self, request, provider, subnodes):
        self.request = request
        self.provider = provider
        self.amount = 0.0
        self.subnodes = subnodes
        self.targets = []

    def __repr__(self):
        return '<AllocationSubRequest for %s (out of %s) of %s from %s>' % (
            self.amount, self.request.amount, self.request.name,
            self.provider.name)

    def addTarget(self, request_target):
        agt = AllocationGrantTarget(self, request_target)
        self.targets.append(agt)
        return agt

    def setAmount(self, amount):
        self.amount = amount

    def getPriority(self):
        return len(self.request.sub_requests)

    def getWaitTime(self):
        return self.request.getWaitTime()

    def grant(self, amount):
        # Grant this request (with the supplied amount).  Adjust this
        # sub-request's value to the actual, as well as the values of
        # any remaining sub-requests.

        # fractional amounts don't make sense
        assert int(amount) == amount

        # Remove from the set of sub-requests so that this is not
        # included in future calculations.
        self.provider.sub_requests.remove(self)
        del self.request.sub_requests[self.provider]
        if amount > 0:
            grant = AllocationGrant(self.request, self.provider,
                                    amount, self.targets)
            self.request.recordGrant(amount)
            # This is now a grant instead of a request.
            self.provider.grants.append(grant)
        else:
            grant = None
            amount = 0
        self.amount = amount
        # Adjust provider and request values accordingly.
        self.request.amount -= amount
        subnode_factor = 1 + self.subnodes
        self.provider.available -= (amount * subnode_factor)
        # Adjust the requested values for related sub-requests.
        self.request.makeRequests()
        # Allocate these granted nodes to targets.
        if grant:
            grant.makeAllocations()


class AllocationGrant(object):
    """A grant of a certain number of nodes of an image from a
    specific provider."""

    def __init__(self, request, provider, amount, targets):
        self.request = request
        self.provider = provider
        self.amount = amount
        self.targets = targets

    def __repr__(self):
        return '<AllocationGrant of %s of %s from %s>' % (
            self.amount, self.request.name, self.provider.name)

    def makeAllocations(self):
        # Allocate this grant to the linked targets.
        total_current = 0
        for agt in self.targets:
            total_current += agt.request_target.current
        amount = self.amount
        # Add the nodes in this allocation to the total number of
        # nodes for this image so that we're setting our target
        # allocations based on a portion of the total future nodes.
        total_current += amount
        remaining_targets = len(self.targets)
        for agt in self.targets:
            # Evenly distribute the grants across all targets
            ratio = 1.0 / remaining_targets
            # Take the weight and apply it to the total number of
            # nodes to this image to figure out how many of the total
            # nodes should ideally be on this target.
            desired_count = int(round(ratio * total_current))
            # The number of nodes off from our calculated target.
            delta = desired_count - agt.request_target.current
            # Use the delta as the allocation for this target, but
            # make sure it's bounded by 0 and the number of nodes we
            # have available to allocate.
            allocation = min(delta, amount)
            allocation = max(allocation, 0)

            # The next time through the loop, we have reduced our
            # grant by this amount.
            amount -= allocation
            # Don't consider this target's count in the total number
            # of nodes in the next iteration, nor the nodes we have
            # just allocated.
            total_current -= agt.request_target.current
            total_current -= allocation
            # Since we aren't considering this target's count, also
            # don't consider this target itself when calculating the
            # ratio.
            remaining_targets -= 1
            # Set the amount of this allocation.
            agt.allocate(allocation)


class AllocationTarget(object):
    """A target to which nodes may be assigned."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return '<AllocationTarget %s>' % (self.name)


class AllocationRequestTarget(object):
    """A request associated with a target to which nodes may be assigned."""
    def __init__(self, request, target, current):
        self.target = target
        self.request = request
        self.current = current


class AllocationGrantTarget(object):
    """A target for a specific grant to which nodes may be assigned."""
    def __init__(self, sub_request, request_target):
        self.sub_request = sub_request
        self.request_target = request_target
        self.amount = 0

    def __repr__(self):
        return '<AllocationGrantTarget for %s of %s to %s>' % (
            self.amount, self.sub_request.request.name,
            self.request_target.target.name)

    def allocate(self, amount):
        # This is essentially the output of this system.  This
        # represents the number of nodes of a specific image from a
        # specific provider that should be assigned to a specific
        # target.
        self.amount = amount
        # Update the number of nodes of this image that are assigned
        # to this target to assist in other allocation calculations
        self.request_target.current += amount

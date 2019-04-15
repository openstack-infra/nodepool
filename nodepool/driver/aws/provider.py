# Copyright 2018 Red Hat
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

import logging
import boto3

from nodepool.driver import Provider
from nodepool.driver.aws.handler import AwsNodeRequestHandler


class AwsInstance:
    def __init__(self, name, metadatas, provider):
        self.id = name
        self.name = name
        self.metadata = {}
        if metadatas:
            for metadata in metadatas:
                if metadata["Key"] == "nodepool_id":
                    self.metadata['nodepool_node_id'] = metadata["Value"]
                    continue
                if metadata["Key"] == "nodepool_pool":
                    self.metadata['nodepool_pool_name'] = metadata["Value"]
                    continue
                if metadata["Key"] == "nodepool_provider":
                    self.metadata['nodepool_provider_name'] = metadata["Value"]
                    continue

    def get(self, name, default=None):
        return getattr(self, name, default)


class AwsProvider(Provider):
    log = logging.getLogger("nodepool.driver.aws.AwsProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ec2 = None

    def getRequestHandler(self, poolworker, request):
        return AwsNodeRequestHandler(poolworker, request)

    def start(self, zk_conn):
        if self.ec2 is not None:
            return True
        self.log.debug("Starting")
        self.aws = boto3.Session(
            region_name=self.provider.region_name,
            profile_name=self.provider.profile_name)
        self.ec2 = self.aws.resource('ec2')

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        for instance in self.ec2.instances.all():
            if instance.state["Name"].lower() == "terminated":
                continue
            ours = False
            if instance.tags:
                for tag in instance.tags:
                    if (tag["Key"] == 'nodepool_provider'
                        and tag["Value"] == self.provider.name):
                        ours = True
                        break
            if not ours:
                continue
            servers.append(AwsInstance(
                instance.id, instance.tags, self.provider))
        return servers

    def countNodes(self, pool=None):
        n = 0
        for instance in self.listNodes():
            if pool is not None:
                if 'nodepool_pool_name' not in instance.metadata:
                    continue
                if pool != instance.metadata['nodepool_pool_name']:
                    continue
            n += 1
        return n

    def getImage(self, image_id):
        return self.ec2.Image(image_id)

    def labelReady(self, label):
        if not label.cloud_image:
            msg = "A cloud-image (AMI) must be supplied with the AWS driver."
            raise Exception(msg)

        image = self.getImage(label.cloud_image.external_name)
        # Image loading is deferred, check if it's really there
        if image.state != 'available':
            self.log.warning(
                "Provider %s is configured to use %s as the AMI for"
                " label %s and that AMI is there but unavailable in the"
                " cloud." % (self.provider.name,
                             label.cloud_image.external_name,
                             label.name))
            return False
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def cleanupNode(self, server_id):
        if self.ec2 is None:
            return False
        instance = self.ec2.Instance(server_id)
        instance.terminate()

    def waitForNodeCleanup(self, server_id):
        # TODO: track instance deletion
        return True

    def createInstance(self, label):
        image_id = label.cloud_image.external_name
        args = dict(
            ImageId=image_id,
            MinCount=1,
            MaxCount=1,
            KeyName=label.key_name,
            InstanceType=label.instance_type,
            NetworkInterfaces=[{
                'AssociatePublicIpAddress': True,
                'DeviceIndex': 0}])

        if label.pool.security_group_id:
            args['NetworkInterfaces'][0]['Groups'] = [
                label.pool.security_group_id
            ]
        if label.pool.subnet_id:
            args['NetworkInterfaces'][0]['SubnetId'] = label.pool.subnet_id

        # Default block device mapping parameters are embedded in AMIs.
        # We might need to supply our own mapping before lauching the instance.
        # We basically want to make sure DeleteOnTermination is true and be
        # able to set the volume type and size.
        image = self.getImage(image_id)
        # TODO: Flavors can also influence whether or not the VM spawns with a
        # volume -- we basically need to ensure DeleteOnTermination is true
        if hasattr(image, 'block_device_mappings'):
            bdm = image.block_device_mappings
            mapping = bdm[0]
            if 'Ebs' in mapping:
                mapping['Ebs']['DeleteOnTermination'] = True
                if label.volume_size:
                    mapping['Ebs']['VolumeSize'] = label.volume_size
                if label.volume_type:
                    mapping['Ebs']['VolumeType'] = label.volume_type
                # If the AMI is a snapshot, we cannot supply an "encrypted"
                # parameter
                if 'Encrypted' in mapping['Ebs']:
                    del mapping['Ebs']['Encrypted']
                args['BlockDeviceMappings'] = [mapping]

        instances = self.ec2.create_instances(**args)
        return self.ec2.Instance(instances[0].id)

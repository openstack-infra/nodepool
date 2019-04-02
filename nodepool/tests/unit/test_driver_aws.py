# Copyright (C) 2018 Red Hat
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

import fixtures
import logging
import os
import tempfile
import time
from unittest.mock import patch

import boto3
from moto import mock_ec2
import yaml

from nodepool import tests
from nodepool import zk


class TestDriverAws(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAws")

    @mock_ec2
    def test_ec2_machine(self):
        aws_id = 'AK000000000000000000'
        aws_key = '0123456789abcdef0123456789abcdef0123456789abcdef'
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_ACCESS_KEY_ID', aws_id))
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_SECRET_ACCESS_KEY', aws_key))

        ec2 = boto3.client('ec2', region_name='us-west-2')

        # TEST-NET-3
        vpc = ec2.create_vpc(CidrBlock='203.0.113.0/24')

        subnet = ec2.create_subnet(
            CidrBlock='203.0.113.128/25', VpcId=vpc['Vpc']['VpcId'])
        subnet_id = subnet['Subnet']['SubnetId']
        sg = ec2.create_security_group(
            GroupName='zuul-nodes', VpcId=vpc['Vpc']['VpcId'],
            Description='Zuul Nodes')
        sg_id = sg['GroupId']

        ec2_template = os.path.join(
            os.path.dirname(__file__), '..', 'fixtures', 'aws.yaml')
        raw_config = yaml.safe_load(open(ec2_template))
        raw_config['zookeeper-servers'][0] = {
            'host': self.zookeeper_host,
            'port': self.zookeeper_port,
            'chroot': self.zookeeper_chroot,
        }
        raw_config['providers'][0]['pools'][0]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][0]['security-group-id'] = sg_id
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(yaml.safe_dump(
                raw_config, default_flow_style=False).encode('utf-8'))
            tf.flush()
            configfile = self.setup_config(tf.name)
            pool = self.useNodepool(configfile, watermark_sleep=1)
            pool.start()
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.node_types.append('ubuntu1404')
            with patch('nodepool.driver.aws.handler.nodescan') as nodescan:
                nodescan.return_value = 'MOCK KEY'
                self.zk.storeNodeRequest(req)

                self.log.debug("Waiting for request %s", req.id)
                req = self.waitForNodeRequest(req)

                self.assertEqual(req.state, zk.FULFILLED)

                self.assertNotEqual(req.nodes, [])
                node = self.zk.getNode(req.nodes[0])
                self.assertEqual(node.allocated_to, req.id)
                self.assertEqual(node.state, zk.READY)
                self.assertIsNotNone(node.launcher)
                self.assertEqual(node.connection_type, 'ssh')
                nodescan.assert_called_with(
                    node.interface_ip,
                    port=22,
                    timeout=180,
                    gather_hostkeys=True)
                # A new request will be paused and for lack of quota until this
                # one is deleted
                req2 = zk.NodeRequest()
                req2.state = zk.REQUESTED
                req2.node_types.append('ubuntu1404')
                self.zk.storeNodeRequest(req2)
                req2 = self.waitForNodeRequest(
                    req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.PENDING)
                # It could flip from PENDING to one of the others, so sleep a
                # bit and be sure
                time.sleep(1)
                req2 = self.waitForNodeRequest(
                    req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.PENDING)

                node.state = zk.DELETING
                self.zk.storeNode(node)

                self.waitForNodeDeletion(node)

                req2 = self.waitForNodeRequest(req2, (zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.FULFILLED)
                node = self.zk.getNode(req2.nodes[0])
                node.state = zk.DELETING
                self.zk.storeNode(node)
                self.waitForNodeDeletion(node)

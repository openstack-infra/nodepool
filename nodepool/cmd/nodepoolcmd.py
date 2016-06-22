#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
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

import argparse
import logging.config
import sys
import time

from nodepool import nodedb
from nodepool import nodepool
from nodepool.version import version_info as npc_version_info
from config_validator import ConfigValidator
from prettytable import PrettyTable

log = logging.getLogger(__name__)


class NodePoolCmd(object):
    def __init__(self):
        self.args = None

    @staticmethod
    def _age(timestamp):
        now = time.time()
        dt = now - timestamp
        m, s = divmod(dt, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        return '%02d:%02d:%02d:%02d' % (d, h, m, s)

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='Node pool.')
        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            default='/etc/nodepool/secure.conf',
                            help='path to secure file')
        parser.add_argument('--version', action='version',
                            version=npc_version_info.version_string(),
                            help='show version')
        parser.add_argument('--debug', dest='debug', action='store_true',
                            help='show DEBUG level logging')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           dest='command',
                                           help='additional help')

        cmd_list = subparsers.add_parser('list', help='list nodes')
        cmd_list.set_defaults(func=self.list)
        cmd_image_list = subparsers.add_parser(
            'image-list', help='list images from providers')
        cmd_image_list.set_defaults(func=self.image_list)

        cmd_dib_image_list = subparsers.add_parser(
            'dib-image-list',
            help='list images built with diskimage-builder')
        cmd_dib_image_list.set_defaults(func=self.dib_image_list)

        cmd_image_update = subparsers.add_parser(
            'image-update',
            help='rebuild the image and upload to provider')
        cmd_image_update.add_argument(
            'provider',
            help='provider name (`all` for uploading to all providers)')
        cmd_image_update.add_argument('image', help='image name')
        cmd_image_update.set_defaults(func=self.image_update)

        cmd_image_build = subparsers.add_parser(
            'image-build',
            help='build image using diskimage-builder')
        cmd_image_build.add_argument('image', help='image name')
        cmd_image_build.set_defaults(func=self.image_build)

        cmd_alien_list = subparsers.add_parser(
            'alien-list',
            help='list nodes not accounted for by nodepool')
        cmd_alien_list.set_defaults(func=self.alien_list)
        cmd_alien_list.add_argument('provider', help='provider name',
                                    nargs='?')

        cmd_alien_image_list = subparsers.add_parser(
            'alien-image-list',
            help='list images not accounted for by nodepool')
        cmd_alien_image_list.set_defaults(func=self.alien_image_list)
        cmd_alien_image_list.add_argument('provider', help='provider name',
                                          nargs='?')

        cmd_hold = subparsers.add_parser(
            'hold',
            help='place a node in the HOLD state')
        cmd_hold.set_defaults(func=self.hold)
        cmd_hold.add_argument('id', help='node id')
        cmd_hold.add_argument('--reason',
                              help='Optional reason this node is held')

        cmd_delete = subparsers.add_parser(
            'delete',
            help='place a node in the DELETE state')
        cmd_delete.set_defaults(func=self.delete)
        cmd_delete.add_argument('id', help='node id')
        cmd_delete.add_argument('--now',
                                action='store_true',
                                help='delete the node in the foreground')

        cmd_image_delete = subparsers.add_parser(
            'image-delete',
            help='delete an image')
        cmd_image_delete.set_defaults(func=self.image_delete)
        cmd_image_delete.add_argument('id', help='image id')

        cmd_dib_image_delete = subparsers.add_parser(
            'dib-image-delete',
            help='delete image built with diskimage-builder')
        cmd_dib_image_delete.set_defaults(func=self.dib_image_delete)
        cmd_dib_image_delete.add_argument('id', help='dib image id')

        cmd_image_upload = subparsers.add_parser(
            'image-upload',
            help='upload an image to a provider ')
        cmd_image_upload.set_defaults(func=self.image_upload)
        cmd_image_upload.add_argument(
            'provider',
            help='provider name (`all` for uploading to all providers)',
            nargs='?', default='all')
        cmd_image_upload.add_argument('image', help='image name')

        cmd_config_validate = subparsers.add_parser(
            'config-validate',
            help='Validate configuration file')
        cmd_config_validate.set_defaults(func=self.config_validate)

        cmd_job_list = subparsers.add_parser('job-list', help='list jobs')
        cmd_job_list.set_defaults(func=self.job_list)

        cmd_job_create = subparsers.add_parser('job-create', help='create job')
        cmd_job_create.add_argument(
            'name',
            help='job name')
        cmd_job_create.add_argument('--hold-on-failure',
                                    help='number of nodes to hold when this job fails')
        cmd_job_create.set_defaults(func=self.job_create)

        cmd_job_delete = subparsers.add_parser(
            'job-delete',
            help='delete job')
        cmd_job_delete.set_defaults(func=self.job_delete)
        cmd_job_delete.add_argument('id', help='job id')

        self.args = parser.parse_args()

    def setup_logging(self):
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG,
                                format='%(asctime)s %(levelname)s %(name)s: '
                                       '%(message)s')
        else:
            logging.basicConfig(level=logging.INFO,
                                format='%(asctime)s %(levelname)s %(name)s: '
                                       '%(message)s')

    def list(self, node_id=None):
        t = PrettyTable(["ID", "Provider", "AZ", "Label", "Target", "Manager",
                         "Hostname", "NodeName", "Server ID", "IP", "State",
                         "Age", "Comment"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for node in session.getNodes():
                if node_id and node.id != node_id:
                    continue
                t.add_row([node.id, node.provider_name, node.az,
                           node.label_name, node.target_name,
                           node.manager_name, node.hostname,
                           node.nodename, node.external_id, node.ip,
                           nodedb.STATE_NAMES[node.state],
                           NodePoolCmd._age(node.state_time),
                           node.comment])
            print t

    def dib_image_list(self):
        t = PrettyTable(["ID", "Image", "Filename", "Version",
                         "State", "Age"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for image in session.getDibImages():
                t.add_row([image.id, image.image_name,
                           image.filename, image.version,
                           nodedb.STATE_NAMES[image.state],
                           NodePoolCmd._age(image.state_time)])
            print t

    def image_list(self):
        t = PrettyTable(["ID", "Provider", "Image", "Hostname", "Version",
                         "Image ID", "Server ID", "State", "Age"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for image in session.getSnapshotImages():
                t.add_row([image.id, image.provider_name, image.image_name,
                           image.hostname, image.version,
                           image.external_id, image.server_external_id,
                           nodedb.STATE_NAMES[image.state],
                           NodePoolCmd._age(image.state_time)])
            print t

    def image_update(self):
        threads = []
        jobs = []

        with self.pool.getDB().getSession() as session:
            self.pool.reconfigureManagers(self.pool.config)
            if self.args.image not in self.pool.config.images_in_use:
                raise Exception("Image specified, %s, is not in use."
                                % self.args.image)

            if self.args.provider == 'all':
                dib_images_built = set()
                for provider in self.pool.config.providers.values():
                    image = provider.images.get(self.args.image)
                    if image and image.diskimage:
                        if image.diskimage not in dib_images_built:
                            self.image_build(image.diskimage)
                            dib_images_built.add(image.diskimage)
                        jobs.append(self.pool.uploadImage(
                            session, provider.name, image.name))
                    elif image:
                        threads.append(self.pool.updateImage(
                            session, provider.name, image.name))
            else:
                provider = self.pool.config.providers.get(self.args.provider)
                if not provider:
                    raise Exception("Provider %s does not exist"
                                    % self.args.provider)
                image = provider.images.get(self.args.image)
                if image and image.diskimage:
                    self.image_build(image.diskimage)
                    jobs.append(self.pool.uploadImage(
                        session, provider.name, image.name))
                elif image:
                    threads.append(self.pool.updateImage(
                        session, provider.name, image.name))
                else:
                    raise Exception("Image %s not in use by provider %s"
                                    % (self.args.image, self.args.provider))

        self._wait_for_threads(threads)
        for job in jobs:
            job.waitForCompletion()

    def image_build(self, diskimage=None):
        diskimage = diskimage or self.args.image
        if diskimage not in self.pool.config.diskimages:
            # only can build disk images, not snapshots
            raise Exception("Trying to build a non disk-image-builder "
                            "image: %s" % diskimage)

        self.pool.buildImage(self.pool.config.diskimages[diskimage])
        self.pool.waitForBuiltImages()

    def image_upload(self):
        self.pool.reconfigureManagers(self.pool.config, False)

        jobs = []

        with self.pool.getDB().getSession() as session:
            if self.args.provider == 'all':
                # iterate for all providers listed in label
                for provider in self.pool.config.providers.values():
                    image = provider.images[self.args.image]
                    if not image.diskimage:
                        self.log.warning("Trying to upload a non "
                                         "disk-image-builder image: %s",
                                         self.args.image)
                    else:
                        jobs.append(self.pool.uploadImage(
                            session, provider.name, self.args.image))
            else:
                provider = self.pool.config.providers[self.args.provider]
                if not provider.images[self.args.image].diskimage:
                    raise Exception("Trying to upload a non "
                                    "disk-image-builder image: %s",
                                    self.args.image)
                jobs.append(self.pool.uploadImage(
                    session, self.args.provider, self.args.image))

        for job in jobs:
            job.waitForCompletion()

    def alien_list(self):
        self.pool.reconfigureManagers(self.pool.config, False)

        t = PrettyTable(["Provider", "Hostname", "Server ID", "IP"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for provider in self.pool.config.providers.values():
                if (self.args.provider and
                        provider.name != self.args.provider):
                    continue
                manager = self.pool.getProviderManager(provider)

                try:
                    for server in manager.listServers():
                        if not session.getNodeByExternalID(
                                provider.name, server['id']):
                            t.add_row([provider.name, server['name'],
                                       server['id'], server['public_v4']])
                except Exception as e:
                    log.warning("Exception listing aliens for %s: %s"
                                % (provider.name, str(e.message)))
        print t

    def alien_image_list(self):
        self.pool.reconfigureManagers(self.pool.config, False)

        t = PrettyTable(["Provider", "Name", "Image ID"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for provider in self.pool.config.providers.values():
                if (self.args.provider and
                        provider.name != self.args.provider):
                    continue
                manager = self.pool.getProviderManager(provider)

                images = []
                try:
                    images = manager.listImages()
                except Exception as e:
                    log.warning("Exception listing alien images for %s: %s"
                                % (provider.name, str(e.message)))

                for image in images:
                    if image['metadata'].get('image_type') == 'snapshot':
                        if not session.getSnapshotImageByExternalID(
                                provider.name, image['id']):
                            t.add_row([provider.name, image['name'],
                                       image['id']])
        print t

    def hold(self):
        node_id = None
        with self.pool.getDB().getSession() as session:
            node = session.getNode(self.args.id)
            node.state = nodedb.HOLD
            if self.args.reason:
                node.comment = self.args.reason
            node_id = node.id
        self.list(node_id=node_id)

    def delete(self):
        if self.args.now:
            self.pool.reconfigureManagers(self.pool.config)
        with self.pool.getDB().getSession() as session:
            node = session.getNode(self.args.id)
            if not node:
                print "Node %s not found." % self.args.id
            elif self.args.now:
                self.pool._deleteNode(session, node)
            else:
                node.state = nodedb.DELETE
                self.list(node_id=node.id)

    def dib_image_delete(self):
        job = None
        self.pool.reconfigureManagers(self.pool.config, False)
        with self.pool.getDB().getSession() as session:
            dib_image = session.getDibImage(self.args.id)
            job = self.pool.deleteDibImage(dib_image)
        job.waitForCompletion()

    def image_delete(self):
        self.pool.reconfigureManagers(self.pool.config, False)
        thread = self.pool.deleteImage(self.args.id)
        self._wait_for_threads((thread, ))

    def config_validate(self):
        validator = ConfigValidator(self.args.config)
        validator.validate()
        log.info("Configuation validation complete")
        #TODO(asselin,yolanda): add validation of secure.conf

    def job_list(self):
        t = PrettyTable(["ID", "Name", "Hold on Failure"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for job in session.getJobs():
                t.add_row([job.id, job.name, job.hold_on_failure])
            print t

    def job_create(self):
        with self.pool.getDB().getSession() as session:
            session.createJob(self.args.name,
                              hold_on_failure=self.args.hold_on_failure)
        self.job_list()

    def job_delete(self):
        with self.pool.getDB().getSession() as session:
            job = session.getJob(self.args.id)
            if not job:
                print "Job %s not found." % self.args.id
            else:
                job.delete()

    def _wait_for_threads(self, threads):
        for t in threads:
            if t:
                t.join()

    def main(self):
        # commands which do not need to start-up or parse config
        if self.args.command in ('config-validate'):
            return self.args.func()

        self.pool = nodepool.NodePool(self.args.secure, self.args.config)
        config = self.pool.loadConfig()
        if self.args.command in ('dib-image-delete', 'dib-image-list',
                                 'image-build', 'image-delete',
                                 'image-upload', 'image-update'):
            self.pool.reconfigureGearmanClient(config)
        self.pool.reconfigureDatabase(config)
        self.pool.setConfig(config)
        self.args.func()


def main():
    npc = NodePoolCmd()
    npc.parse_arguments()
    npc.setup_logging()
    return npc.main()


if __name__ == "__main__":
    sys.exit(main())

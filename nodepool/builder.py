#!/usr/bin/env python
# Copyright 2015 Hewlett-Packard Development Company, L.P.
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

import errno
import json
import logging
import os
import subprocess
import threading
import time
import traceback

import gear
import shlex
from stats import statsd

import nodedb


# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"


class NodePoolBuilder(object):
    log = logging.getLogger("nodepool.builder")

    def __init__(self, nodepool):
        self.nodepool = nodepool
        self._running = False
        self._start_lock = threading.Lock()
        self._gearman_worker = None

    @property
    def running(self):
        return self._running

    def start(self):
        with self._start_lock:
            if self._running:
                raise RuntimeError('Cannot start, already running.')
            self._running = True

            self._gearman_worker = self._initializeGearmanWorker(
                self.nodepool.config.gearman_servers.values())
            images = self.nodepool.config.diskimages.keys()
            self._registerGearmanFunctions(images)
            self.thread = threading.Thread(target=self._run,
                                           name='NodePool Builder')
            self.thread.start()

    def stop(self):
        with self._start_lock:
            self.log.debug('Stopping')
            if not self._running:
                self.log.warning("Stop called when we are already stopped.")
                return

            self._running = False

            self._gearman_worker.stopWaitingForJobs()

            self.log.debug('Waiting for builder thread to complete.')
            self.thread.join()
            self.log.debug('Builder thread completed.')

            try:
                self._gearman_worker.shutdown()
            except OSError as e:
                if e.errno == errno.EBADF:
                    # The connection has been lost already
                    self.log.debug("Gearman connection lost when "
                                   "attempting to shutdown. Ignoring.")
                else:
                    raise

    def _run(self):
        self.log.debug('Starting listener for build jobs')
        while self._running:
            try:
                job = self._gearman_worker.getJob()
                self._handleJob(job)
            except gear.InterruptedError:
                pass
            except Exception:
                self.log.exception('Exception while getting job')

    def _initializeGearmanWorker(self, servers):
        worker = gear.Worker('Nodepool Builder')
        for server in servers:
            worker.addServer(server.host, server.port)

        self.log.debug('Waiting for gearman server')
        worker.waitForServer()
        return worker

    def _registerGearmanFunctions(self, images):
        self.log.debug('Registering gearman functions')
        for image in images:
            self._gearman_worker.registerFunction('image-build:%s' % image)

    def _handleJob(self, job):
        try:
            if job.name.startswith('image-build:'):
                self.log.debug('got job %s with data %s',
                               job.name, job.arguments)
                args = json.loads(job.arguments)
                self.buildImage(args['image_id'])
                job.sendWorkComplete()
            else:
                self.log.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.log.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())

    def _buildImage(self, image, image_name, filename):
        env = os.environ.copy()

        env['DIB_RELEASE'] = image.release
        env['DIB_IMAGE_NAME'] = image_name
        env['DIB_IMAGE_FILENAME'] = filename
        # Note we use a reference to the nodepool config here so
        # that whenever the config is updated we get up to date
        # values in this thread.
        if self.nodepool.config.elementsdir:
            env['ELEMENTS_PATH'] = self.nodepool.config.elementsdir
        if self.nodepool.config.scriptdir:
            env['NODEPOOL_SCRIPTDIR'] = self.nodepool.config.scriptdir

        # send additional env vars if needed
        for k, v in image.env_vars.items():
            env[k] = v

        img_elements = image.elements
        img_types = ",".join(image.image_types)

        qemu_img_options = ''
        if 'qcow2' in img_types:
            qemu_img_options = DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS

        if 'fake-' in filename:
            dib_cmd = 'nodepool/tests/fake-image-create'
        else:
            dib_cmd = 'disk-image-create'

        cmd = ('%s -x -t %s --no-tmpfs %s -o %s %s' %
               (dib_cmd, img_types, qemu_img_options, filename, img_elements))

        log = logging.getLogger("nodepool.image.build.%s" %
                                (image_name,))

        self.log.info('Running %s' % cmd)

        try:
            p = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env)
        except OSError as e:
            raise Exception("Failed to exec '%s'. Error: '%s'" %
                            (cmd, e.strerror))

        while True:
            ln = p.stdout.readline()
            log.info(ln.strip())
            if not ln:
                break

        p.wait()
        ret = p.returncode
        if ret:
            raise Exception("DIB failed creating %s" % (filename,))

    def buildImage(self, image_id):
        with self.nodepool.getDB().getSession() as session:
            image = session.getDibImage(image_id)
            self.log.info("Creating image: %s with filename %s" %
                          (image.image_name, image.filename))

            start_time = time.time()
            timestamp = int(start_time)
            image.version = timestamp
            session.commit()

            # retrieve image details
            image_details = \
                self.nodepool.config.diskimages[image.image_name]
            try:
                self._buildImage(
                    image_details,
                    image.image_name,
                    image.filename)
            except Exception:
                self.log.exception("Exception building DIB image %s:" %
                                   (image_id,))
                # DIB should've cleaned up after itself, just remove this
                # image from the DB.
                image.delete()
                return

            image.state = nodedb.READY
            session.commit()
            self.log.info("DIB image %s with file %s is built" % (
                image_id, image.filename))

            if statsd:
                dt = int((time.time() - start_time) * 1000)
                key = 'nodepool.dib_image_build.%s' % image.image_name
                statsd.timing(key, dt)
                statsd.incr(key)

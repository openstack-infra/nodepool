# Copyright (C) 2015 Hewlett-Packard Development Company, L.P.
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

import os
import time
import threading

import fixtures

from nodepool import builder, exceptions, fakeprovider, tests


class TestNodepoolBuilderDibImage(tests.BaseTestCase):
    def test_from_path(self):
        image = builder.DibImageFile.from_path(
            '/foo/bar/myid1234.qcow2')
        self.assertEqual(image.image_id, 'myid1234')
        self.assertEqual(image.extension, 'qcow2')

    def test_from_image_id(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path = os.path.join(tempdir.path, 'myid1234.qcow2')
        open(image_path, 'w')

        images = builder.DibImageFile.from_image_id(tempdir.path, 'myid1234')
        self.assertEqual(len(images), 1)

        image = images[0]
        self.assertEqual(image.image_id, 'myid1234')
        self.assertEqual(image.extension, 'qcow2')

    def test_from_id_multiple(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path_1 = os.path.join(tempdir.path, 'myid1234.qcow2')
        image_path_2 = os.path.join(tempdir.path, 'myid1234.raw')
        open(image_path_1, 'w')
        open(image_path_2, 'w')

        images = builder.DibImageFile.from_image_id(tempdir.path, 'myid1234')
        images = sorted(images, key=lambda x: x.extension)
        self.assertEqual(len(images), 2)

        self.assertEqual(images[0].extension, 'qcow2')
        self.assertEqual(images[1].extension, 'raw')

    def test_from_images_dir(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path_1 = os.path.join(tempdir.path, 'myid1234.qcow2')
        image_path_2 = os.path.join(tempdir.path, 'myid1234.raw')
        open(image_path_1, 'w')
        open(image_path_2, 'w')

        images = builder.DibImageFile.from_images_dir(tempdir.path)
        images = sorted(images, key=lambda x: x.extension)
        self.assertEqual(len(images), 2)

        self.assertEqual(images[0].image_id, 'myid1234')
        self.assertEqual(images[0].extension, 'qcow2')
        self.assertEqual(images[1].image_id, 'myid1234')
        self.assertEqual(images[1].extension, 'raw')

    def test_to_path(self):
        image = builder.DibImageFile('myid1234', 'qcow2')
        self.assertEqual(image.to_path('/imagedir'),
                         '/imagedir/myid1234.qcow2')
        self.assertEqual(image.to_path('/imagedir/'),
                         '/imagedir/myid1234.qcow2')
        self.assertEqual(image.to_path('/imagedir/', False),
                         '/imagedir/myid1234')

        image = builder.DibImageFile('myid1234')
        self.assertRaises(exceptions.BuilderError, image.to_path, '/imagedir/')

class TestNodepoolBuilder(tests.DBTestCase):
    def test_start_stop(self):
        config = self.setup_config('node_dib.yaml')
        nb = builder.NodePoolBuilder(config)
        nb_thread = threading.Thread(target=nb.runForever)
        nb_thread.daemon = True

        nb_thread.start()
        while not nb.running:
            time.sleep(.5)

        nb.stop()
        while nb_thread.isAlive():
            time.sleep(.5)

    def test_image_upload_fail(self):
        """Test that image upload fails are handled properly."""
        # Enter a working state before we test that fails are handled.
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)

        # Now swap out the upload fake so that the next uploads fail
        fake_client = fakeprovider.FakeUploadFailCloud()

        def get_fake_client(*args, **kwargs):
            return fake_client

        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.provider_manager.ProviderManager._getClient',
            get_fake_client))
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.nodepool._get_one_cloud',
            fakeprovider.fake_get_one_cloud))

        provider = pool.config.providers['fake-dib-provider']
        pool.getProviderManager(provider).resetClient()

        # Delete our existing image forcing a reupload
        with pool.getDB().getSession() as session:
            images = session.getSnapshotImages()
            self.assertEqual(len(images), 1)
            pool.deleteImage(images[0].id)

        # Now wait for image uploads to fail at least once
        # cycling out the existing snap image and making a new one
        first_fail_id = None
        while True:
            with pool.getDB().getSession() as session:
                images = session.getSnapshotImages()
                if not images:
                    continue
                elif images and not first_fail_id:
                    first_fail_id = images[0].id
                elif first_fail_id != images[0].id:
                    # We failed to upload first_fail_id and have
                    # moved onto another upload that will fail.
                    break

class TestZookeeper(tests.ZKTestCase):
    def test_zk(self):
        self.zkclient.get('/')

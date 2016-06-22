# Copyright (C) 2011-2014 OpenStack Foundation
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

import time

# States:
# The cloud provider is building this machine.  We have an ID, but it's
# not ready for use.
BUILDING = 1
# The machine is ready for use.
READY = 2
# This can mean in-use, or used but complete.
USED = 3
# Delete this machine immediately.
DELETE = 4
# Keep this machine indefinitely.
HOLD = 5
# Acceptance testing (pre-ready)
TEST = 6


STATE_NAMES = {
    BUILDING: 'building',
    READY: 'ready',
    USED: 'used',
    DELETE: 'delete',
    HOLD: 'hold',
    TEST: 'test',
    }

from sqlalchemy import Table, Column, Integer, String, \
    MetaData, create_engine
from sqlalchemy.orm import scoped_session, mapper, relationship, foreign
from sqlalchemy.orm.session import Session, sessionmaker

metadata = MetaData()

dib_image_table = Table(
    'dib_image', metadata,
    Column('id', Integer, primary_key=True),
    Column('image_name', String(255), index=True, nullable=False),
    # Image filename
    Column('filename', String(255)),
    # Version indicator (timestamp)
    Column('version', Integer),
    # One of the above values
    Column('state', Integer),
    # Time of last state change
    Column('state_time', Integer),
    mysql_engine='InnoDB',
    )
snapshot_image_table = Table(
    'snapshot_image', metadata,
    Column('id', Integer, primary_key=True),
    Column('provider_name', String(255), index=True, nullable=False),
    Column('image_name', String(255), index=True, nullable=False),
    # Image hostname
    Column('hostname', String(255)),
    # Version indicator (timestamp)
    Column('version', Integer),
    # Provider assigned id for this image
    Column('external_id', String(255)),
    # Provider assigned id of the server used to create the snapshot
    Column('server_external_id', String(255)),
    # One of the above values
    Column('state', Integer),
    # Time of last state change
    Column('state_time', Integer),
    mysql_engine='InnoDB',
    )
node_table = Table(
    'node', metadata,
    Column('id', Integer, primary_key=True),
    Column('provider_name', String(255), index=True, nullable=False),
    Column('label_name', String(255), index=True, nullable=False),
    Column('target_name', String(255), index=True, nullable=False),
    Column('manager_name', String(255)),
    # Machine name
    Column('hostname', String(255), index=True),
    # Eg, jenkins node name
    Column('nodename', String(255), index=True),
    # Provider assigned id for this machine
    Column('external_id', String(255)),
    # Provider availability zone for this machine
    Column('az', String(255)),
    # Primary IP address
    Column('ip', String(255)),
    # Internal/fixed IP address
    Column('ip_private', String(255)),
    # One of the above values
    Column('state', Integer),
    # Time of last state change
    Column('state_time', Integer),
    # Comment about the state of the node - used to annotate held nodes
    Column('comment', String(255)),
    mysql_engine='InnoDB',
    )
subnode_table = Table(
    'subnode', metadata,
    Column('id', Integer, primary_key=True),
    Column('node_id', Integer, index=True, nullable=False),
    # Machine name
    Column('hostname', String(255), index=True),
    # Provider assigned id for this machine
    Column('external_id', String(255)),
    # Primary IP address
    Column('ip', String(255)),
    # Internal/fixed IP address
    Column('ip_private', String(255)),
    # One of the above values
    Column('state', Integer),
    # Time of last state change
    Column('state_time', Integer),
    mysql_engine='InnoDB',
    )
job_table = Table(
    'job', metadata,
    Column('id', Integer, primary_key=True),
    # The name of the job
    Column('name', String(255), index=True),
    # Automatically hold up to this number of nodes that fail this job
    Column('hold_on_failure', Integer),
    mysql_engine='InnoDB',
    )


class DibImage(object):
    def __init__(self, image_name, filename=None, version=None,
                 state=BUILDING):
        self.image_name = image_name
        self.filename = filename
        self.version = version
        self.state = state

    def delete(self):
        session = Session.object_session(self)
        session.delete(self)
        session.commit()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        self.state_time = int(time.time())
        session = Session.object_session(self)
        if session:
            session.commit()


class SnapshotImage(object):
    def __init__(self, provider_name, image_name, hostname=None, version=None,
                 external_id=None, server_external_id=None, state=BUILDING):
        self.provider_name = provider_name
        self.image_name = image_name
        self.hostname = hostname
        self.version = version
        self.external_id = external_id
        self.server_external_id = server_external_id
        self.state = state

    def delete(self):
        session = Session.object_session(self)
        session.delete(self)
        session.commit()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        self.state_time = int(time.time())
        session = Session.object_session(self)
        if session:
            session.commit()


class Node(object):
    def __init__(self, provider_name, label_name, target_name, az,
                 hostname=None, external_id=None, ip=None, ip_private=None,
                 manager_name=None, state=BUILDING, comment=None):
        self.provider_name = provider_name
        self.label_name = label_name
        self.target_name = target_name
        self.manager_name = manager_name
        self.external_id = external_id
        self.az = az
        self.ip = ip
        self.ip_private = ip_private
        self.hostname = hostname
        self.state = state
        self.comment = comment

    def delete(self):
        session = Session.object_session(self)
        session.delete(self)
        session.commit()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        self.state_time = int(time.time())
        session = Session.object_session(self)
        if session:
            session.commit()


class SubNode(object):
    def __init__(self, node,
                 hostname=None, external_id=None, ip=None, ip_private=None,
                 state=BUILDING):
        self.node_id = node.id
        self.provider_name = node.provider_name
        self.label_name = node.label_name
        self.target_name = node.target_name
        self.external_id = external_id
        self.ip = ip
        self.ip_private = ip_private
        self.hostname = hostname
        self.state = state

    def delete(self):
        session = Session.object_session(self)
        session.delete(self)
        session.commit()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        self.state_time = int(time.time())
        session = Session.object_session(self)
        if session:
            session.commit()


class Job(object):
    def __init__(self, name=None, hold_on_failure=0):
        self.name = name
        self.hold_on_failure = hold_on_failure

    def delete(self):
        session = Session.object_session(self)
        session.delete(self)
        session.commit()


mapper(Job, job_table)


mapper(SubNode, subnode_table,
       properties=dict(_state=subnode_table.c.state))


mapper(Node, node_table,
       properties=dict(
           _state=node_table.c.state,
           subnodes=relationship(
               SubNode,
               cascade='all, delete-orphan',
               uselist=True,
               primaryjoin=foreign(subnode_table.c.node_id) == node_table.c.id,
               backref='node')))


mapper(SnapshotImage, snapshot_image_table,
       properties=dict(_state=snapshot_image_table.c.state))

mapper(DibImage, dib_image_table,
       properties=dict(_state=dib_image_table.c.state))


class NodeDatabase(object):
    def __init__(self, dburi):
        engine_kwargs = dict(echo=False, pool_recycle=3600)
        if 'sqlite:' not in dburi:
            engine_kwargs['max_overflow'] = -1

        self.engine = create_engine(dburi, **engine_kwargs)
        metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = scoped_session(self.session_factory)

    def getSession(self):
        return NodeDatabaseSession(self.session)


class NodeDatabaseSession(object):
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, etype, value, tb):
        if etype:
            self.session().rollback()
        else:
            self.session().commit()
        self.session().close()
        self.session = None

    def abort(self):
        self.session().rollback()

    def commit(self):
        self.session().commit()

    def delete(self, obj):
        self.session().delete(obj)

    def getProviders(self):
        return [
            x.provider_name for x in
            self.session().query(SnapshotImage).distinct(
                snapshot_image_table.c.provider_name).all()]

    def getDibImages(self, state=None):
        exp = self.session().query(DibImage).order_by(
            dib_image_table.c.image_name)
        if state:
            exp = exp.filter(dib_image_table.c.state == state)
        return exp.all()

    def getImages(self, provider_name):
        return [
            x.image_name for x in
            self.session().query(SnapshotImage).filter(
                snapshot_image_table.c.provider_name == provider_name
                ).distinct(snapshot_image_table.c.image_name).all()]

    def getSnapshotImages(self, state=None):
        exp = self.session().query(SnapshotImage).order_by(
            snapshot_image_table.c.provider_name,
            snapshot_image_table.c.image_name)
        if state:
            exp = exp.filter(snapshot_image_table.c.state == state)
        return exp.all()

    def getDibImage(self, image_id):
        images = self.session().query(DibImage).filter_by(
            id=image_id).all()
        if not images:
            return None
        return images[0]

    def getBuildingDibImagesByName(self, image_name):
        images = self.session().query(DibImage).filter(
            dib_image_table.c.image_name == image_name,
            dib_image_table.c.state == BUILDING).all()
        if not images:
            return None
        return images

    def getSnapshotImage(self, image_id):
        images = self.session().query(SnapshotImage).filter_by(
            id=image_id).all()
        if not images:
            return None
        return images[0]

    def getSnapshotImageByExternalID(self, provider_name, external_id):
        images = self.session().query(SnapshotImage).filter_by(
            provider_name=provider_name,
            external_id=external_id).all()
        if not images:
            return None
        return images[0]

    def getOrderedReadyDibImages(self, image_name):
        images = self.session().query(DibImage).filter(
            dib_image_table.c.image_name == image_name,
            dib_image_table.c.state == READY).order_by(
                dib_image_table.c.version.desc()).all()
        return images

    def getOrderedReadySnapshotImages(self, provider_name, image_name):
        images = self.session().query(SnapshotImage).filter(
            snapshot_image_table.c.provider_name == provider_name,
            snapshot_image_table.c.image_name == image_name,
            snapshot_image_table.c.state == READY).order_by(
                snapshot_image_table.c.version.desc()).all()
        return images

    def getCurrentSnapshotImage(self, provider_name, image_name):
        images = self.getOrderedReadySnapshotImages(provider_name, image_name)

        if not images:
            return None
        return images[0]

    def createDibImage(self, *args, **kwargs):
        new = DibImage(*args, **kwargs)
        self.session().add(new)
        self.commit()
        return new

    def createSnapshotImage(self, *args, **kwargs):
        new = SnapshotImage(*args, **kwargs)
        self.session().add(new)
        self.commit()
        return new

    def getNodes(self, provider_name=None, label_name=None, target_name=None,
                 state=None):
        exp = self.session().query(Node).order_by(
            node_table.c.provider_name,
            node_table.c.label_name)
        if provider_name:
            exp = exp.filter_by(provider_name=provider_name)
        if label_name:
            exp = exp.filter_by(label_name=label_name)
        if target_name:
            exp = exp.filter_by(target_name=target_name)
        if state:
            exp = exp.filter(node_table.c.state == state)
        return exp.all()

    def createNode(self, *args, **kwargs):
        new = Node(*args, **kwargs)
        self.session().add(new)
        self.commit()
        return new

    def createSubNode(self, *args, **kwargs):
        new = SubNode(*args, **kwargs)
        self.session().add(new)
        self.commit()
        return new

    def getNode(self, id):
        nodes = self.session().query(Node).filter_by(id=id).all()
        if not nodes:
            return None
        return nodes[0]

    def getSubNode(self, id):
        nodes = self.session().query(SubNode).filter_by(id=id).all()
        if not nodes:
            return None
        return nodes[0]

    def getNodeByHostname(self, hostname):
        nodes = self.session().query(Node).filter_by(hostname=hostname).all()
        if not nodes:
            return None
        return nodes[0]

    def getNodeByNodename(self, nodename):
        nodes = self.session().query(Node).filter_by(nodename=nodename).all()
        if not nodes:
            return None
        return nodes[0]

    def getNodeByExternalID(self, provider_name, external_id):
        nodes = self.session().query(Node).filter_by(
            provider_name=provider_name,
            external_id=external_id).all()
        if not nodes:
            return None
        return nodes[0]

    def getJob(self, id):
        jobs = self.session().query(Job).filter_by(id=id).all()
        if not jobs:
            return None
        return jobs[0]

    def getJobByName(self, name):
        jobs = self.session().query(Job).filter_by(name=name).all()
        if not jobs:
            return None
        return jobs[0]

    def getJobs(self):
        return self.session().query(Job).all()

    def createJob(self, *args, **kwargs):
        new = Job(*args, **kwargs)
        self.session().add(new)
        self.commit()
        return new

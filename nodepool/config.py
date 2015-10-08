from six.moves import configparser as ConfigParser
import yaml


class ConfigValue(object):
    def __eq__(self, other):
        if isinstance(other, ConfigValue):
            if other.__dict__ == self.__dict__:
                return True
        return False


class Config(ConfigValue):
    pass


class Provider(ConfigValue):
    pass


class ProviderImage(ConfigValue):
    pass


class Target(ConfigValue):
    pass


class Label(ConfigValue):
    pass


class LabelProvider(ConfigValue):
    pass


class Cron(ConfigValue):
    pass


class ZMQPublisher(ConfigValue):
    pass


class GearmanServer(ConfigValue):
    pass


class DiskImage(ConfigValue):
    pass


class Network(ConfigValue):
    pass


def loadConfig(config_path, cloud_config):
    config = yaml.load(open(config_path))

    newconfig = Config()
    newconfig.db = None
    newconfig.dburi = None
    newconfig.providers = {}
    newconfig.targets = {}
    newconfig.labels = {}
    newconfig.scriptdir = config.get('script-dir')
    newconfig.elementsdir = config.get('elements-dir')
    newconfig.imagesdir = config.get('images-dir')
    newconfig.dburi = None
    newconfig.provider_managers = {}
    newconfig.jenkins_managers = {}
    newconfig.zmq_publishers = {}
    newconfig.gearman_servers = {}
    newconfig.diskimages = {}
    newconfig.crons = {}

    for name, default in [
        ('image-update', '14 2 * * *'),
        ('cleanup', '* * * * *'),
        ('check', '*/15 * * * *'),
        ]:
        c = Cron()
        c.name = name
        newconfig.crons[c.name] = c
        c.job = None
        c.timespec = config.get('cron', {}).get(name, default)

    for addr in config['zmq-publishers']:
        z = ZMQPublisher()
        z.name = addr
        z.listener = None
        newconfig.zmq_publishers[z.name] = z

    for server in config.get('gearman-servers', []):
        g = GearmanServer()
        g.host = server['host']
        g.port = server.get('port', 4730)
        g.name = g.host + '_' + str(g.port)
        newconfig.gearman_servers[g.name] = g

    for provider in config['providers']:
        p = Provider()
        p.name = provider['name']
        newconfig.providers[p.name] = p

        cloud_kwargs = _cloudKwargsFromProvider(provider)
        p.cloud_config = _get_one_cloud(cloud_config, cloud_kwargs)
        p.region_name = provider.get('region-name')
        p.max_servers = provider['max-servers']
        p.keypair = provider.get('keypair', None)
        p.pool = provider.get('pool')
        p.rate = provider.get('rate', 1.0)
        p.api_timeout = provider.get('api-timeout')
        p.boot_timeout = provider.get('boot-timeout', 60)
        p.launch_timeout = provider.get('launch-timeout', 3600)
        p.use_neutron = bool(provider.get('networks', ()))
        p.networks = []
        for network in provider.get('networks', []):
            n = Network()
            p.networks.append(n)
            if 'net-id' in network:
                n.id = network['net-id']
                n.name = None
            elif 'net-label' in network:
                n.name = network['net-label']
                n.id = None
            else:
                n.name = network.get('name')
                n.id = None
            n.public = network.get('public', False)
        p.ipv6_preferred = provider.get('ipv6-preferred')
        p.azs = provider.get('availability-zones')
        p.template_hostname = provider.get(
            'template-hostname',
            'template-{image.name}-{timestamp}'
        )
        p.image_type = provider.get('image-type', 'qcow2')
        p.images = {}
        for image in provider['images']:
            i = ProviderImage()
            i.name = image['name']
            p.images[i.name] = i
            i.base_image = image.get('base-image', None)
            i.min_ram = image['min-ram']
            i.name_filter = image.get('name-filter', None)
            i.setup = image.get('setup', None)
            i.diskimage = image.get('diskimage', None)
            i.username = image.get('username', 'jenkins')
            i.user_home = image.get('user-home', '/home/jenkins')
            i.private_key = image.get('private-key',
                                      '/var/lib/jenkins/.ssh/id_rsa')
            i.config_drive = image.get('config-drive', None)

            # note this does "double-duty" -- for
            # SnapshotImageUpdater the meta-data dict is passed to
            # nova when the snapshot image is created.  For
            # DiskImageUpdater, this dict is expanded and used as
            # custom properties when the image is uploaded.
            i.meta = image.get('meta', {})
            # 5 elements, and no key or value can be > 255 chars
            # per novaclient.servers.create() rules
            if i.meta:
                if len(i.meta) > 5 or \
                   any([len(k) > 255 or len(v) > 255
                        for k, v in i.meta.iteritems()]):
                    # soft-fail
                    #self.log.error("Invalid metadata for %s; ignored"
                    #               % i.name)
                    i.meta = {}

    if 'diskimages' in config:
        for diskimage in config['diskimages']:
            d = DiskImage()
            d.name = diskimage['name']
            newconfig.diskimages[d.name] = d
            if 'elements' in diskimage:
                d.elements = u' '.join(diskimage['elements'])
            else:
                d.elements = ''
            # must be a string, as it's passed as env-var to
            # d-i-b, but might be untyped in the yaml and
            # interpreted as a number (e.g. "21" for fedora)
            d.release = str(diskimage.get('release', ''))
            d.env_vars = diskimage.get('env-vars', {})
            if not isinstance(d.env_vars, dict):
                #self.log.error("%s: ignoring env-vars; "
                #               "should be a dict" % d.name)
                d.env_vars = {}
            d.image_types = set()
        # Do this after providers to build the image-types
        for provider in newconfig.providers.values():
            for image in provider.images.values():
                if (image.diskimage and
                    image.diskimage in newconfig.diskimages):
                    diskimage = newconfig.diskimages[image.diskimage]
                    diskimage.image_types.add(provider.image_type)

    for label in config['labels']:
        l = Label()
        l.name = label['name']
        newconfig.labels[l.name] = l
        l.image = label['image']
        l.min_ready = label.get('min-ready', 2)
        l.subnodes = label.get('subnodes', 0)
        l.ready_script = label.get('ready-script')
        l.providers = {}
        for provider in label['providers']:
            p = LabelProvider()
            p.name = provider['name']
            l.providers[p.name] = p

    for target in config['targets']:
        t = Target()
        t.name = target['name']
        newconfig.targets[t.name] = t
        jenkins = target.get('jenkins', {})
        t.online = True
        t.rate = target.get('rate', 1.0)
        t.jenkins_test_job = jenkins.get('test-job')
        t.jenkins_url = None
        t.jenkins_user = None
        t.jenkins_apikey = None
        t.jenkins_credentials_id = None

        t.hostname = target.get(
            'hostname',
            '{label.name}-{provider.name}-{node_id}'
        )
        t.subnode_hostname = target.get(
            'subnode-hostname',
            '{label.name}-{provider.name}-{node_id}-{subnode_id}'
        )

    # A set of image names that are in use by labels, to be
    # used by the image update methods to determine whether
    # a given image needs to be updated.
    newconfig.images_in_use = set()
    for label in newconfig.labels.values():
        if label.min_ready >= 0:
            newconfig.images_in_use.add(label.image)

    return newconfig


def loadSecureConfig(config, secure_config_path):
    secure = ConfigParser.ConfigParser()
    secure.readfp(open(secure_config_path))

    config.dburi = secure.get('database', 'dburi')

    for target in config.targets.values():
        section_name = 'jenkins "%s"' % target.name
        if secure.has_section(section_name):
            target.jenkins_url = secure.get(section_name, 'url')
            target.jenkins_user = secure.get(section_name, 'user')
            target.jenkins_apikey = secure.get(section_name, 'apikey')

        try:
            target.jenkins_credentials_id = secure.get(
                section_name, 'credentials')
        except:
            pass


def _cloudKwargsFromProvider(provider):
    cloud_kwargs = {}
    for arg in ['region-name', 'api-timeout', 'cloud']:
        if arg in provider:
            cloud_kwargs[arg] = provider[arg]

    # These are named from back when we only talked to Nova. They're
    # actually compute service related
    if 'service-type' in provider:
        cloud_kwargs['compute-service-type'] = provider['service-type']
    if 'service-name' in provider:
        cloud_kwargs['compute-service-name'] = provider['service-name']

    auth_kwargs = {}
    for auth_key in (
            'username', 'password', 'auth-url', 'project-id', 'project-name'):
        if auth_key in provider:
            auth_kwargs[auth_key] = provider[auth_key]

    cloud_kwargs['auth'] = auth_kwargs
    return cloud_kwargs


def _get_one_cloud(cloud_config, cloud_kwargs):
    '''This is a function to allow for overriding it in tests.'''
    return cloud_config.get_one_cloud(**cloud_kwargs)

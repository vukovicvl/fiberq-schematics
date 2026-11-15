"""Public release information shared by the plugin and its help dialog."""

from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
_metadata = ConfigParser(interpolation=None)
_metadata.read(ROOT / 'metadata.txt', encoding='utf-8')
INFO = dict(_metadata['general'])
NAME = INFO['name']
VERSION = INFO['version']
AUTHOR = INFO['author']
EMAIL = INFO['email']
HOMEPAGE = INFO['homepage']
REPOSITORY = INFO['repository']
TRACKER = INFO['tracker']
DONATE = 'https://github.com/sponsors/vukovicvl'

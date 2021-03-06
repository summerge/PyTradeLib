# This file is part of PyTradeLib.
#
# Copyright 2013 Brian A Cappello <briancappello at gmail>
#
# PyTradeLib is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PyTradeLib is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with PyTradeLib.  If not, see http://www.gnu.org/licenses/

import gevent.monkey
gevent.monkey.patch_socket()

import os
import sys
import errno
import time
import urllib2
import gevent
from decorator import decorator

try: import simplejson as json
except: import json
try: import cPickle as pickle
except: import pickle

from pytradelib.bar import (FrequencyToStr, StrToFrequency)
from pytradelib import settings


def printf(*args):
    for i, arg in enumerate(args):
        if len(args) > 1 and i+1 != len(args):
            print arg,
        else:
            print arg
    sys.stdout.flush()

## --- string utils ---------------------------------------------------------
@decorator
def lower(fn, obj, string, *args, **kwargs):
    return fn(obj, string.lower(), *args, **kwargs)

def try_dict_str_values_to_float(dict_):
    # try to convert remaining strings to float
    for key, value in dict_.items():
        if isinstance(value, str):
            # first assume the value is a quoted number
            try:
                if '.' in value:
                    dict_[key] = float(value)
                else:
                    dict_[key] = int(value)
            except ValueError:
                # then assume the value is a large number suffixed with K/M/B/T
                try:
                    dict_[key] = convert_KMBT_str_to_int(value)
                # otherwise just leave the value as a string
                except ValueError:
                    pass
    return dict_

def convert_KMBT_str_to_int(value):
    def to_float(value):
        return float(value[:-1])
    if value.endswith('K'):
        value = to_float(value) * 1000
    elif value.endswith('M'):
        value = to_float(value) * 1000000
    elif value.endswith('B'):
        value = to_float(value) * 1000000000
    elif value.endswith('T'):
        value = to_float(value) * 1000000000000
    else:
        raise ValueError
    return int(value)


## --- file utils ---------------------------------------------------------
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError, e:
        if e.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise e

def get_extension(compression_type):
    if settings.DATA_COMPRESSION == None:
        extension = 'csv'
    elif settings.DATA_COMPRESSION == 'lz4':
        extension = 'csv.lz4'
    elif settings.DATA_COMPRESSION == 'gz':
        extension = 'csv.gz'
    return extension

def supports_seeking(compression_type):
    if compression_type in [None, 'gz']:
        return True
    return False

def slug(string):
    return string.lower().replace(' ', '_').replace('&', 'and')

def get_historical_file_name(symbol, frequency, provider_format, compression):
    return '.'.join([
        '_'.join([
            symbol,
            FrequencyToStr[frequency],
            provider_format,
            ]),
        get_extension(compression),
        ]).lower().replace(' ', '-')

def symbol_from_file_name(file_name):
    return file_name.split('_')[0].lower()

def symbol_from_file_path(file_path):
    return symbol_from_file_name(os.path.basename(file_path))

def frequency_from_file_name(file_name):
    return StrToFrequency[file_name.split('_')[1]]

def frequency_from_file_path(file_path):
    return frequency_from_file_name(os.path.basename(file_path))

def save_to_json(data, file_path):
    with open(file_path, 'w') as f:
        f.write(json.dumps(data))

def load_from_json(file_path):
    with open(file_path, 'r') as f:
        return json.loads(f.read())

def save_to_pickle(data, file_path):
    with open(file_path, 'w') as f:
        f.write(pickle.dumps(data))

def load_from_pickle(file_path):
    with open(file_path, 'r') as f:
        return pickle.loads(f.read())


## --- multiprocessing/threading/gevent utils ------------------------------
def batch(list_, size=None, sleep=None):
    size = size or 100
    total_batches = len(list_)/size + 1
    for i in xrange(total_batches):
        lowerIdx = size * i
        upperIdx = size * (i+1)
        if upperIdx == lowerIdx or upperIdx == len(list_):
            break
        if upperIdx <= len(list_):
            yield list_[lowerIdx:upperIdx]
        else:
            yield list_[lowerIdx:]

        if sleep and upperIdx < len(list_):
            time.sleep(sleep)


## --- downloading utils ---------------------------------------------------
def download(url, context=None):
    context = context or {}
    if not isinstance(context, dict):
        printf('WARNING: context should be supplied as a dict! Converting.')
        context = {'context': context}
    context['url'] = url
    context['error'] = None

    # try downloading the url; return on success, retry on various URLErrors and
    #  (gracefully) fail on HTTP 404 errors. unrecognized exceptions still get raised.
    while True:
        try:
            response = urllib2.urlopen(url)
        except urllib2.HTTPError as e:
            if '404' in str(e):
                context['error'] = str(e)
                return (None, context)
            else:
                printf(context['url'])
                raise e
        except (urllib2.URLError, Exception) as e:
            error = str(e).lower()
            if 'server failed' in error \
              or 'misformatted query' in error:
                time.sleep(0.1)
                printf('retrying download of %s' % url)
            elif 'connection reset by peer' in error \
              or 'request timed out' in error:
                time.sleep(0.5)
                printf('retrying download of %s' % url)
            else:
                raise e
        else:
            data = response.read()
            return (data, context)

def bulk_download(urls_andor_contexts):
    '''
    :type urls_andor_tags_list: a list of urls or a list of tuple(url, tag)s
    '''
    threads = []
    for params in urls_andor_contexts:
        if isinstance(params, tuple):
            threads.append(gevent.spawn(download, *params))
        else:
            threads.append(gevent.spawn(download, params))
    gevent.joinall(threads)
    for thread in threads:
        yield thread.value

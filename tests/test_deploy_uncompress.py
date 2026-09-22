# coding: utf-8
from contextlib import contextmanager
import os
from shutil import rmtree
import zipfile

import pytest

from flask import request

from scrapydweb.vars import PY2
from scrapydweb.views.operations.deploy import DeployUploadView


SCRAPY_CFG_CONTENT = """
[settings]
default = demo.settings

[deploy]
url = http://localhost:6800/
project = demo
"""

# b'\xff\xff' is illegal in both utf-8 and gbk/cp936
ILLEGAL_BYTES = b'\xff\xffillegal'


@contextmanager
def make_view(app):
    with app.test_request_context('/1/deploy/upload/'):
        request.view_args = {'node': 1}
        yield DeployUploadView()


def write_windows_cp936_zip(zip_path, members):
    """Build a zip file simulating Windows CN (cp936) "send to zipped":
    filenames are stored as gbk bytes without the UTF-8 flag.
    A member name given as bytes is stored as the raw bytes.
    """
    orig = zipfile.ZipInfo._encodeFilenameFlags

    def _encode_filename_flags_gbk(self):
        try:
            return self.filename.encode('ascii'), self.flag_bits & ~0x800
        except UnicodeEncodeError:
            try:
                return self.filename.encode('gbk'), self.flag_bits & ~0x800
            except UnicodeEncodeError:
                return self.filename.encode('cp437'), self.flag_bits & ~0x800

    zipfile.ZipInfo._encodeFilenameFlags = _encode_filename_flags_gbk
    try:
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for name, content in members:
                if not isinstance(name, str):
                    name = name.decode('cp437')
                zf.writestr(name, content)
    finally:
        zipfile.ZipInfo._encodeFilenameFlags = orig


def test_decode_zip_filename():
    decode = DeployUploadView.decode_zip_filename
    # ASCII names are kept as-is
    assert decode('demo/scrapy.cfg') == 'demo/scrapy.cfg'
    # Mojibake decoded as cp437 from gbk bytes is recovered to the original Chinese name
    mojibake = u'中文目录'.encode('gbk').decode('cp437')
    assert decode(mojibake) == u'中文目录'
    # Entries with the UTF-8 flag set are trusted and returned directly
    assert decode(mojibake, flag_bits=0x800) == mojibake
    assert decode(u'中文目录', flag_bits=0x800) == u'中文目录'
    # Illegal bytes undecodable in both utf-8 and gbk fall back to the original name
    illegal = ILLEGAL_BYTES.decode('cp437')
    assert decode(illegal) == illegal


def test_sanitize_zip_filename():
    sanitize = DeployUploadView.sanitize_zip_filename
    assert sanitize('demo/scrapy.cfg') == 'demo/scrapy.cfg'
    assert sanitize(u'中文目录/scrapy.cfg') == u'中文目录/scrapy.cfg'
    # Windows style separators are normalized
    assert sanitize('demo\\demo\\settings.py') == 'demo/demo/settings.py'
    # Zip-slip parts are stripped out
    assert sanitize('../..\\evil/scrapy.cfg') == 'evil/scrapy.cfg'
    assert sanitize('/absolute/path/scrapy.cfg') == 'absolute/path/scrapy.cfg'
    # Surrogates from illegal bytes are replaced to keep the pathname utf-8 encodable
    sanitized = sanitize(u'\udcff\udcffillegal/scrapy.cfg')
    sanitized.encode('utf-8')
    assert sanitized.endswith('/scrapy.cfg')


def test_uncompress_windows_cp936_zip(app, tmpdir):
    zip_path = str(tmpdir.join('windows_cp936.zip'))
    write_windows_cp936_zip(zip_path, [
        (u'中文目录/', ''),
        (u'中文目录/scrapy.cfg', SCRAPY_CFG_CONTENT),
        (u'中文目录/demo/__init__.py', ''),
        (u'中文目录/demo/settings.py', 'BOT_NAME = "demo"'),
    ])
    # Double-check the zip really stores gbk bytes without the UTF-8 flag
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            assert not info.flag_bits & 0x800

    with make_view(app) as view:
        tmpdir_extracted = view.uncompress_to_tmpdir(zip_path)
        try:
            assert os.path.isdir(os.path.join(tmpdir_extracted, u'中文目录'))
            view.search_scrapy_cfg_path(tmpdir_extracted)
            assert view.scrapy_cfg_path == os.path.join(
                os.path.abspath(tmpdir_extracted), u'中文目录', 'scrapy.cfg')
        finally:
            rmtree(tmpdir_extracted, ignore_errors=True)


def test_uncompress_nested_scrapy_cfg(app, tmpdir):
    zip_path = str(tmpdir.join('nested.zip'))
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('outer/', '')
        zf.writestr(u'outer/inner 目录/project/scrapy.cfg', SCRAPY_CFG_CONTENT)
        zf.writestr(u'outer/inner 目录/project/demo/settings.py', 'BOT_NAME = "demo"')

    with make_view(app) as view:
        tmpdir_extracted = view.uncompress_to_tmpdir(zip_path)
        try:
            view.search_scrapy_cfg_path(tmpdir_extracted)
            assert view.scrapy_cfg_path == os.path.join(
                os.path.abspath(tmpdir_extracted), 'outer', u'inner 目录', 'project', 'scrapy.cfg')
        finally:
            rmtree(tmpdir_extracted, ignore_errors=True)


def test_uncompress_illegal_filename_mixed(app, tmpdir):
    zip_path = str(tmpdir.join('illegal_mixed.zip'))
    write_windows_cp936_zip(zip_path, [
        (u'中文目录/scrapy.cfg', SCRAPY_CFG_CONTENT),
        (u'中文目录/demo/settings.py', 'BOT_NAME = "demo"'),
        # Illegal filename bytes undecodable in both utf-8 and gbk
        (ILLEGAL_BYTES + b'/junk.txt', 'junk'),
        (u'中文目录'.encode('gbk') + b'/' + ILLEGAL_BYTES + b'.txt', 'junk'),
    ])

    with make_view(app) as view:
        tmpdir_extracted = view.uncompress_to_tmpdir(zip_path)
        try:
            # The valid project files are extracted despite the illegal members
            assert os.path.isfile(os.path.join(tmpdir_extracted, u'中文目录', 'scrapy.cfg'))
            view.search_scrapy_cfg_path(tmpdir_extracted)
            assert view.scrapy_cfg_path == os.path.join(
                os.path.abspath(tmpdir_extracted), u'中文目录', 'scrapy.cfg')
            # All recorded searched paths are utf-8 encodable for the error reporting page
            for path in view.scrapy_cfg_searched_paths:
                path.encode('utf-8')
        finally:
            rmtree(tmpdir_extracted, ignore_errors=True)


@pytest.mark.skipif(PY2, reason="surrogateescape pathnames only exist in PY3")
def test_search_scrapy_cfg_with_surrogate_dir(app, tmpdir):
    # Simulate illegal filenames decoded via surrogateescape,
    # e.g. b'\xff\xffillegal' from "touch $(echo -e '\xff\xffillegal')" on Linux
    illegal_dirname = os.fsdecode(ILLEGAL_BYTES)
    assert '\\udcff' in repr(illegal_dirname)

    project_dir = os.path.join(str(tmpdir), u'中文项目')
    os.makedirs(project_dir)
    with open(os.path.join(project_dir, 'scrapy.cfg'), 'w') as f:
        f.write(SCRAPY_CFG_CONTENT)

    # macOS forbids creating files with invalid utf-8 bytes,
    # so a fake walk is used to simulate the illegal pathnames
    def fake_walk(top):
        yield str(tmpdir), [u'中文项目', illegal_dirname], []
        yield project_dir, [illegal_dirname], ['scrapy.cfg']

    with make_view(app) as view:
        view.search_scrapy_cfg_path(str(tmpdir), func_walk=fake_walk)
        assert view.scrapy_cfg_path == os.path.join(
            os.path.abspath(str(tmpdir)), u'中文项目', 'scrapy.cfg')
        for path in view.scrapy_cfg_searched_paths:
            path.encode('utf-8')


def test_search_scrapy_cfg_not_found(app, tmpdir):
    os.mkdir(os.path.join(str(tmpdir), 'demo_without_scrapy_cfg'))

    with make_view(app) as view:
        view.search_scrapy_cfg_path(str(tmpdir))
        assert view.scrapy_cfg_path == ''
        assert view.scrapy_cfg_searched_paths
        for path in view.scrapy_cfg_searched_paths:
            path.encode('utf-8')

# coding: utf-8
"""Regression tests for uncompressing zip files from Windows with
Chinese/illegal pathnames and locating scrapy.cfg in PY3,
without requiring a running Scrapyd server."""
import logging
import os
import shutil
import zipfile

import pytest

from scrapydweb.views.operations import deploy
from scrapydweb.views.operations.deploy import (
    DeployUploadView,
    normalize_zip_member_name,
    safe_path_for_display,
)
from tests.utils import cst

SCRAPY_CFG_CONTENT = (
    "[settings]\n"
    "default = demo.settings\n"
    "\n"
    "[deploy]\n"
    "url = http://localhost:6800/\n"
    "project = demo\n"
)

GBK_MOJIBAKE = u'副本'.encode('gbk').decode('cp437')  # As decoded by zipfile in PY3
UTF8_MOJIBAKE = u'副本'.encode('utf-8').decode('cp437')  # UTF-8 bytes without the UTF-8 flag


class WindowsGbkZipInfo(zipfile.ZipInfo):
    """Simulate a zip member created on Windows_CN:
    filename encoded in GBK without the UTF-8 flag."""
    def _encodeFilenameFlags(self):
        try:
            return self.filename.encode('ascii'), self.flag_bits
        except UnicodeEncodeError:
            return self.filename.encode('gbk'), self.flag_bits & ~0x800


class RawBytesZipInfo(zipfile.ZipInfo):
    """Simulate a zip member with raw filename bytes neither valid UTF-8 nor GBK."""
    raw = b''
    def _encodeFilenameFlags(self):
        return self.raw, self.flag_bits & ~0x800


def make_zip(zip_path, members, info_class=None):
    """members: list of (name, content) tuples; content None means a directory."""
    with zipfile.ZipFile(str(zip_path), 'w') as zf:
        for name, content in members:
            if info_class is not None:
                info = info_class(name)
            else:
                info = zipfile.ZipInfo(name)
            if content is None:
                info.external_attr = 0o40775 << 16
                zf.writestr(info, '')
            else:
                zf.writestr(info, content)
    return str(zip_path)


def make_view():
    view = DeployUploadView.__new__(DeployUploadView)
    view.logger = logging.getLogger('test_deploy_uncompress')
    view.scrapy_cfg_path = ''
    view.scrapy_cfg_searched_paths = []
    return view


@pytest.fixture
def view(monkeypatch):
    flashes = []
    monkeypatch.setattr(deploy, 'flash', lambda msg, category: flashes.append((msg, category)))
    view = make_view()
    view.flashes = flashes
    return view


def assert_paths_utf8_safe(root):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            path.encode('utf-8')  # UnicodeEncodeError if containing surrogates


# normalize_zip_member_name

def test_normalize_name_windows_cp936():
    assert normalize_zip_member_name(GBK_MOJIBAKE + u'/scrapy.cfg') == u'副本/scrapy.cfg'


def test_normalize_name_utf8_without_flag():
    assert normalize_zip_member_name(UTF8_MOJIBAKE + u'/scrapy.cfg') == u'副本/scrapy.cfg'


def test_normalize_name_proper_utf8():
    assert normalize_zip_member_name(u'中文目录/嵌套/scrapy.cfg') == u'中文目录/嵌套/scrapy.cfg'
    assert normalize_zip_member_name('demo/scrapy.cfg') == 'demo/scrapy.cfg'


def test_normalize_name_illegal_surrogates():
    assert normalize_zip_member_name(u'demo/\udc8b\udc8billegal/scrapy.cfg') == 'demo/__illegal/scrapy.cfg'


def test_normalize_name_unsafe_parts():
    assert normalize_zip_member_name('../../evil/scrapy.cfg') == 'evil/scrapy.cfg'
    assert normalize_zip_member_name('..\\..\\evil\\scrapy.cfg') == 'evil/scrapy.cfg'
    assert normalize_zip_member_name('/abs/path/scrapy.cfg') == 'abs/path/scrapy.cfg'
    assert normalize_zip_member_name('./demo/./scrapy.cfg') == 'demo/scrapy.cfg'
    assert normalize_zip_member_name('..') == ''


# safe_path_for_display

def test_safe_path_for_display():
    assert safe_path_for_display(u'/tmp/中文') == u'/tmp/中文'
    displayed = safe_path_for_display(u'/tmp/\udc8b\udc8billegal')
    displayed.encode('utf-8')  # Should not raise UnicodeEncodeError
    assert 'illegal' in displayed


# uncompress_to_tmpdir + search_scrapy_cfg_path

def test_windows_cp936_zip_with_chinese_dirs(view, tmp_path):
    zip_path = make_zip(tmp_path / 'windows_cp936.zip', [
        (u'中文目录/', None),
        (u'中文目录/嵌套目录/', None),
        (u'中文目录/嵌套目录/scrapy.cfg', SCRAPY_CFG_CONTENT),
        (u'中文目录/嵌套目录/中文文件.txt', 'content'),
    ], info_class=WindowsGbkZipInfo)

    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        assert_paths_utf8_safe(tmpdir)
        view.search_scrapy_cfg_path(tmpdir)
        expected = os.path.join(tmpdir, u'中文目录', u'嵌套目录', 'scrapy.cfg')
        assert view.scrapy_cfg_path == expected
        with open(expected) as f:
            assert f.read() == SCRAPY_CFG_CONTENT
        assert os.path.exists(os.path.join(tmpdir, u'中文目录', u'嵌套目录', u'中文文件.txt'))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_nested_scrapy_cfg(view, tmp_path):
    zip_path = make_zip(tmp_path / 'nested.zip', [
        ('outer/', None),
        ('outer/inner/', None),
        ('outer/inner/demo/', None),
        ('outer/inner/demo/scrapy.cfg', SCRAPY_CFG_CONTENT),
        ('outer/inner/demo/demo/__init__.py', ''),
    ])

    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        view.search_scrapy_cfg_path(tmpdir)
        assert view.scrapy_cfg_path == os.path.join(tmpdir, 'outer', 'inner', 'demo', 'scrapy.cfg')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mixed_illegal_and_valid_members(view, tmp_path):
    # A member with raw filename bytes invalid in both UTF-8 and GBK,
    # mixed with a valid nested project directory in GBK encoding.
    class IllegalZipInfo(RawBytesZipInfo):
        raw = b'illegal_\xff\xfe_dir/illegal_\xff\xfe_file.txt'

    zip_path = str(tmp_path / 'mixed.zip')
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr(IllegalZipInfo('placeholder'), 'illegal content')
        zf.writestr(WindowsGbkZipInfo(u'中文项目/'), '')
        zf.writestr(WindowsGbkZipInfo(u'中文项目/scrapy.cfg'), SCRAPY_CFG_CONTENT)

    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        assert_paths_utf8_safe(tmpdir)
        view.search_scrapy_cfg_path(tmpdir)
        assert view.scrapy_cfg_path == os.path.join(tmpdir, u'中文项目', 'scrapy.cfg')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_search_with_illegal_pathnames_on_disk(view, tmp_path):
    # Files with illegal filenames may reside in the uncompressed directory,
    # e.g. extracted from a tar.gz with GBK filenames by tarfile with surrogateescape.
    illegal_dir = os.path.join(str(tmp_path), u'\udc8b\udc8billegal')
    try:
        os.mkdir(illegal_dir)
        with open(os.path.join(illegal_dir, u'\udc8b\udc8bfile.txt'), 'w') as f:
            f.write('illegal')
    except (OSError, UnicodeError):
        pytest.skip('The filesystem does not support illegal filenames')

    project_dir = os.path.join(str(tmp_path), u'中文项目', 'demo')
    os.makedirs(project_dir)
    with open(os.path.join(project_dir, 'scrapy.cfg'), 'w') as f:
        f.write(SCRAPY_CFG_CONTENT)

    view.search_scrapy_cfg_path(str(tmp_path))
    assert view.scrapy_cfg_path == os.path.join(project_dir, 'scrapy.cfg')
    for path in view.scrapy_cfg_searched_paths:
        path.encode('utf-8')  # Should be safe for the error page


def test_search_scrapy_cfg_not_found_display_safe(view, tmp_path):
    illegal_dir = os.path.join(str(tmp_path), u'\udc8b\udc8billegal')
    try:
        os.mkdir(illegal_dir)
    except (OSError, UnicodeError):
        pytest.skip('The filesystem does not support illegal filenames')

    view.search_scrapy_cfg_path(str(tmp_path))
    assert view.scrapy_cfg_path == ''
    assert view.scrapy_cfg_searched_paths
    for path in view.scrapy_cfg_searched_paths:
        path.encode('utf-8')  # Should not raise UnicodeEncodeError when rendering the error page


def test_search_with_illegal_pathnames_simulated(view, tmp_path):
    # Simulate os.walk() yielding dirpaths with surrogates in PY3,
    # e.g. extracted from a tar.gz with GBK filenames by tarfile with surrogateescape.
    project_dir = os.path.join(str(tmp_path), u'中文项目')
    os.makedirs(project_dir)
    with open(os.path.join(project_dir, 'scrapy.cfg'), 'w') as f:
        f.write(SCRAPY_CFG_CONTENT)
    illegal_dir = os.path.join(str(tmp_path), u'\udc8b\udc8billegal')

    def fake_walk(top):
        yield top, [u'中文项目', u'\udc8b\udc8billegal'], []
        yield illegal_dir, [], [u'\udc8b\udc8bfile.txt']
        yield project_dir, [], ['scrapy.cfg']

    view.search_scrapy_cfg_path(str(tmp_path), func_walk=fake_walk)
    assert view.scrapy_cfg_path == os.path.join(project_dir, 'scrapy.cfg')
    for path in view.scrapy_cfg_searched_paths:
        path.encode('utf-8')  # Should be safe for the error page


def test_search_not_found_with_illegal_pathnames_simulated(view, tmp_path):
    illegal_dir = os.path.join(str(tmp_path), u'\udc8b\udc8billegal')

    def fake_walk(top):
        yield top, [u'\udc8b\udc8billegal'], []
        yield illegal_dir, [], [u'\udc8b\udc8bfile.txt']

    view.search_scrapy_cfg_path(str(tmp_path), func_walk=fake_walk)
    assert view.scrapy_cfg_path == ''
    assert view.scrapy_cfg_searched_paths
    for path in view.scrapy_cfg_searched_paths:
        path.encode('utf-8')  # Should not raise UnicodeEncodeError when rendering the error page


# Real fixture zips in tests/data (extracted from tests/data.zip by conftest)

DATA_DIR = os.path.join(cst.ROOT_DIR, 'data')
ZIPS_WITH_SCRAPY_CFG = [
    'demo_inner.zip',
    'demo_outer.zip',
    'demo - Win7CNsendzipped.zip',
    'demo - Win10cp1252.zip',
    u'副本.zip',
    u'demo - 副本 - Win7CN.zip',
    u'demo - 副本 - Win7CNsendzipped.zip',
    u'demo - 副本 - Win10cp936.zip',
    u'demo - 副本 - macOS.zip',
    u'demo - 副本 - Ubuntu.zip',
]


@pytest.mark.parametrize('filename', ZIPS_WITH_SCRAPY_CFG)
def test_fixture_zips_scrapy_cfg_found(view, filename):
    zip_path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(zip_path):
        pytest.skip('Fixture not found: %s' % zip_path)
    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        assert_paths_utf8_safe(tmpdir)
        view.search_scrapy_cfg_path(tmpdir)
        assert view.scrapy_cfg_path, 'scrapy.cfg not found in %s' % filename
        assert os.path.basename(view.scrapy_cfg_path) == 'scrapy.cfg'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_fixture_zip_windows_cp936_recovered(view):
    zip_path = os.path.join(DATA_DIR, u'demo - 副本 - Win10cp936.zip')
    if not os.path.exists(zip_path):
        pytest.skip('Fixture not found: %s' % zip_path)
    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        assert u'demo - 副本 - Win10cp936' in os.listdir(tmpdir)
        view.search_scrapy_cfg_path(tmpdir)
        assert view.scrapy_cfg_path == os.path.join(
            tmpdir, u'demo - 副本 - Win10cp936', 'scrapy.cfg')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_fixture_zip_without_scrapy_cfg(view):
    zip_path = os.path.join(DATA_DIR, 'demo_without_scrapy_cfg.zip')
    if not os.path.exists(zip_path):
        pytest.skip('Fixture not found: %s' % zip_path)
    tmpdir = view.uncompress_to_tmpdir(zip_path)
    try:
        view.search_scrapy_cfg_path(tmpdir)
        assert view.scrapy_cfg_path == ''
        assert view.scrapy_cfg_searched_paths
        for path in view.scrapy_cfg_searched_paths:
            path.encode('utf-8')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

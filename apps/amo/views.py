from collections import defaultdict
import json
import re

from django import http
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import commonware.log
import jingo
import waffle
from django_arecibo.tasks import post
from django_statsd.views import record as django_statsd_record
from django_statsd.clients import statsd

import amo
import api
from api.views import render_xml
import files.tasks
from amo.decorators import no_login_required, post_required
from amo.utils import log_cef
from amo.context_processors import get_collect_timings
from . import monitors

log = commonware.log.getLogger('z.amo')
monitor_log = commonware.log.getLogger('z.monitor')
jp_log = commonware.log.getLogger('z.jp.repack')

flash_re = re.compile(r'^(Win|(PPC|Intel) Mac OS X|Linux.+i\d86)|SunOs', re.IGNORECASE)
quicktime_re = re.compile(r'^(application/(sdp|x-(mpeg|rtsp|sdp))|audio/(3gpp(2)?|AMR|aiff|basic|mid(i)?|mp4|mpeg|vnd\.qcelp|wav|x-(aiff|m4(a|b|p)|midi|mpeg|wav))|image/(pict|png|tiff|x-(macpaint|pict|png|quicktime|sgi|targa|tiff))|video/(3gpp(2)?|flc|mp4|mpeg|quicktime|sd-video|x-mpeg))$')
java_re = re.compile(r'^application/x-java-((applet|bean)(;jpi-version=1\.5|;version=(1\.(1(\.[1-3])?|(2|4)(\.[1-2])?|3(\.1)?|5)))?|vm)$')
wmp_re = re.compile(r'^(application/(asx|x-(mplayer2|ms-wmp))|video/x-ms-(asf(-plugin)?|wm(p|v|x)?|wvx)|audio/x-ms-w(ax|ma))$')

@never_cache
@no_login_required
def monitor(request, format=None):

    # For each check, a boolean pass/fail status to show in the template
    status_summary = {}
    results = {}

    checks = ['memcache', 'libraries', 'elastic', 'path', 'redis', 'hera']

    for check in checks:
        with statsd.timer('monitor.%s' % check) as timer:
            status, result = getattr(monitors, check)()
        status_summary[check] = status
        results['%s_results' % check] = result
        results['%s_timer' % check] = timer.ms

    # If anything broke, send HTTP 500.
    status_code = 200 if all(status_summary.values()) else 500

    if format == '.json':
        return http.HttpResponse(json.dumps(status_summary),
                                 status=status_code)
    ctx = {}
    ctx.update(results)
    ctx['status_summary'] = status_summary

    return jingo.render(request, 'services/monitor.html',
                        ctx, status=status_code)


def robots(request):
    """Generate a robots.txt"""
    _service = (request.META['SERVER_NAME'] == settings.SERVICES_DOMAIN)
    if _service or not settings.ENGAGE_ROBOTS:
        template = "User-agent: *\nDisallow: /"
    else:
        template = jingo.render(request, 'amo/robots.html',
                                {'apps': amo.APP_USAGE})

    return HttpResponse(template, mimetype="text/plain")


def handler404(request):
    webapp = settings.APP_PREVIEW
    template = 'amo/404%s.html' % ('_apps' if webapp else '')
    if request.path_info.startswith('/api/'):
        # Pass over to handler404 view in api if api was targeted
        return api.views.handler404(request)
    else:
        return jingo.render(request, template, {'webapp': webapp}, status=404)


def handler500(request):
    webapp = settings.APP_PREVIEW
    template = 'amo/500%s.html' % ('_apps' if webapp else '')
    arecibo = getattr(settings, 'ARECIBO_SERVER_URL', '')
    if arecibo:
        post(request, 500)
    if request.path_info.startswith('/api/'):
        return api.views.handler500(request)
    else:
        return jingo.render(request, template, {'webapp': webapp}, status=500)


def csrf_failure(request, reason=''):
    webapp = settings.APP_PREVIEW
    template = 'amo/403%s.html' % ('_apps' if webapp else '')
    return jingo.render(request, template,
                        {'csrf': 'CSRF' in reason, 'webapp': webapp},
                        status=403)


def loaded(request):
    return http.HttpResponse('%s' % request.META['wsgi.loaded'],
                             content_type='text/plain')


@csrf_exempt
@require_POST
def cspreport(request):
    """Accept CSP reports and log them."""
    report = ('blocked-uri', 'violated-directive', 'original-policy')

    if not waffle.sample_is_active('csp-store-reports'):
        return HttpResponse()

    try:
        v = json.loads(request.raw_post_data)['csp-report']
        # CEF module wants a dictionary of environ, we want request
        # to be the page with error on it, that's contained in the csp-report
        # so we need to modify the meta before we pass in to the logger
        meta = request.META.copy()
        method, url = v['request'].split(' ', 1)
        meta.update({'REQUEST_METHOD': method, 'PATH_INFO': url})
        v = [(k, v[k]) for k in report if k in v]
        log_cef('CSP Violation', 5, meta, username=request.user,
                signature='CSPREPORT',
                msg='A client reported a CSP violation',
                cs7=v, cs7Label='ContentPolicy')
    except Exception, e:
        log.debug('Exception in CSP report: %s' % e, exc_info=True)
        return HttpResponseBadRequest()

    return HttpResponse()


@csrf_exempt
@post_required
def builder_pingback(request):
    data = dict(request.POST.items())
    jp_log.info('Pingback from builder: %r' % data)
    try:
        # We expect all these attributes to be available.
        attrs = 'result msg location secret request'.split()
        for attr in attrs:
            assert attr in data, '%s not in %s' % (attr, data)
        # Only AMO and the builder should know this secret.
        assert data.get('secret') == settings.BUILDER_SECRET_KEY
    except Exception:
        jp_log.warning('Problem with builder pingback.', exc_info=True)
        return http.HttpResponseBadRequest()
    files.tasks.repackage_jetpack(data)
    return http.HttpResponse()


def graphite(request, site):
    ctx = {'width': 586, 'height': 308}
    ctx.update(request.GET.items())
    ctx['site'] = site
    return jingo.render(request, 'services/graphite.html', ctx)


@csrf_exempt
@post_required
def record(request):
    # The rate limiting is done up on the client, but if things go wrong
    # we can just turn the percentage down to zero.
    if get_collect_timings():
        return django_statsd_record(request)
    return http.HttpResponseForbidden()


def plugin_check(request):

    g = defaultdict(str, [(k, v) for k, v in request.GET.iteritems()])

    required = ['mimetype', 'appID', 'appVersion', 'clientOS', 'chromeLocale']

    # Some defaults we override depending on what we find below.
    plugin = dict(mimetype='-1', name='-1', guid='-1', version=None,
                  iconUrl=None, XPILocation=None, InstallerLocation=None,
                  InstallerHash=None, InstallerShowsUI=None,
                  manualInstallationURL=None, licenseURL=None,
                  needsRestart='true')

    # Special case for mimetype if they are provided.
    plugin['mimetype'] = g['mimetype'] or '-1'

    for s in required:
        if s not in request.GET:
            # A sort of 404, matching what was returned in the original PHP.
            return render_xml(request, 'services/plugin_check.xml',
                              {'plugin': plugin})

    # Figure out what plugins we've got, and what plugins we know where
    # to get.

    # Begin our huge and embarrassing if-else statement.
    if (g['mimetype'] in ['application/x-shockwave-flash',
                          'application/futuresplash'] and
        re.match(flash_re, g['clientOS'])):

        # We really want the regexp for Linux to be /Linux(?! x86_64)/ but
        # for now we can't tell 32-bit linux appart from 64-bit linux, so
        # feed all x86_64 users the flash player, even if it's a 32-bit
        # plugin.

        # We've got flash plugin installers for Win and Linux (x86),
        # present those to the user, and for Mac users, tell them where
        # they can go to get the installer.

        plugin.update(
            name='Adobe Flash Player',
            manualInstallationURL='http://www.adobe.com/go/getflashplayer')

        # Don't use a https URL for the license here, per request from
        # Macromedia.

        if g['clientOS'].startswith('Win'):
            plugin.update(
                guid='{4cfaef8a-a6c9-41a0-8e6f-967eb8f49143}',
                XPILocation=None,
                iconUrl='http://fpdownload2.macromedia.com/pub/flashplayer/current/fp_win_installer.ico',
                needsRestart='false',
                InstallerShowsUI='true')

            if re.match(r'^(?!.*(Win64|x64))Win.*$', g['clientOS']):
                plugin.update(
                    version='11.0.1.152',
                    InstallerHash='sha256:de413e759589b387bfa17852a91e752e0318104998e0e4bbddacd9972483ec12',
                    InstallerLocation='http://download.macromedia.com/pub/flashplayer/current/FP_PL_PFS_INSTALLER_32bit.exe')
            else:
                plugin.update(
                    version='11.0.1.152 64-bit',
                    InstallerHash='sha256:c31e5238b00b284e64969262161fd170a3288e34d3ddcc95b50b558de7a74744',
                    InstallerLocation='http://download.macromedia.com/pub/flashplayer/current/FP_PL_PFS_INSTALLER_64bit.exe')

    elif (g['mimetype'] == 'application/x-director' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='Adobe Shockwave Player',
            manualInstallationURL='http://get.adobe.com/shockwave/')

        # Even though the shockwave installer is not a silent installer, we
        # need to show its EULA here since we've got a slimmed down
        # installer that doesn't do that itself.
        if g['chromeLocale'] != 'ja-JP':
            plugin.update(
                licenseURL='http://www.adobe.com/go/eula_shockwaveplayer')
        else:
            plugin.update(
                licenseURL='http://www.adobe.com/go/eula_shockwaveplayer_jp')
        plugin.update(
            guid='{45f2a22c-4029-4209-8b3d-1421b989633f}',
            XPILocation=None,
            version='11.6.3.633',
            InstallerHash='sha256:c0096c13456f112516542a5fc568fd904839ffcad9d6b7c741f1528fb4e93ca5',
            InstallerLocation='http://fpdownload.macromedia.com/pub/shockwave/default/english/win95nt/latest/Shockwave_Installer_FF.exe',
            needsRestart='false',
            InstallerShowsUI='false')

    elif (g['mimetype'] in ['audio/x-pn-realaudio-plugin',
                            'audio/x-pn-realaudio'] and
          re.match(r'^(Win|Linux|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='Real Player',
            version='10.5',
            manualInstallationURL='http://www.real.com')

        if g['clientOS'].startswith('Win'):
            plugin.update(
                XPILocation='http://forms.real.com/real/player/download.html?type=firefox',
                guid='{d586351c-cb55-41a7-8e7b-4aaac5172d39}')
        else:
            plugin.update(
                guid='{269eb771-59de-4702-9209-ca97ce522f6d}')

    elif (re.match(quicktime_re, g['mimetype']) and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):

        # Well, we don't have a plugin that can handle any of those
        # mimetypes, but the Apple Quicktime plugin can. Point the user to
        # the Quicktime download page.

        plugin.update(
            name='Apple Quicktime',
            guid='{a42bb825-7eee-420f-8ee7-834062b6fefd}',
            InstallerShowsUI='true',
            manualInstallationURL='http://www.apple.com/quicktime/download/')

    elif (re.match(java_re, g['mimetype']) and
          re.match(r'^(Win|Linux|PPC Mac OS X)', g['clientOS'])):

        # We serve up the Java plugin for the following mimetypes:
        #
        # application/x-java-vm
        # application/x-java-applet;jpi-version=1.5
        # application/x-java-bean;jpi-version=1.5
        # application/x-java-applet;version=1.3
        # application/x-java-bean;version=1.3
        # application/x-java-applet;version=1.2.2
        # application/x-java-bean;version=1.2.2
        # application/x-java-applet;version=1.2.1
        # application/x-java-bean;version=1.2.1
        # application/x-java-applet;version=1.4.2
        # application/x-java-bean;version=1.4.2
        # application/x-java-applet;version=1.5
        # application/x-java-bean;version=1.5
        # application/x-java-applet;version=1.3.1
        # application/x-java-bean;version=1.3.1
        # application/x-java-applet;version=1.4
        # application/x-java-bean;version=1.4
        # application/x-java-applet;version=1.4.1
        # application/x-java-bean;version=1.4.1
        # application/x-java-applet;version=1.2
        # application/x-java-bean;version=1.2
        # application/x-java-applet;version=1.1.3
        # application/x-java-bean;version=1.1.3
        # application/x-java-applet;version=1.1.2
        # application/x-java-bean;version=1.1.2
        # application/x-java-applet;version=1.1.1
        # application/x-java-bean;version=1.1.1
        # application/x-java-applet;version=1.1
        # application/x-java-bean;version=1.1
        # application/x-java-applet
        # application/x-java-bean
        #
        #
        # We don't have a Java plugin to offer here, but Sun's got one for
        # Windows. For other platforms we know where to get one, point the
        # user to the JRE download page.

        plugin.update(
            name='Java Runtime Environment',
            version='1.6 u29',
            manualInstallationURL='http://java.com/downloads',
            InstallerShowsUI='false',
            needsRestart='false')

        # For now, send Vista users to a manual download page.
        #
        # This is a temp fix for bug 366129 until vista has a non-manual
        # solution.
        if g['clientOS'].startswith('Windows NT 6.0'):
            plugin.update(
                guid='{fbe640ef-4375-4f45-8d79-767d60bf75b8}',
                InstallerLocation='http://java.com/firefoxjre_exe',
                InstallerHash='sha1:4951aadb74b69d5840c3a3fee6ae45e9cc064fe3')
        elif g['clientOS'].startswith('Win'):
            plugin.update(
                guid='{92a550f2-dfd2-4d2f-a35d-a98cfda73595}',
                InstallerLocation='http://java.com/firefoxjre_exe',
                InstallerHash='sha1:4951aadb74b69d5840c3a3fee6ae45e9cc064fe3',
                XPILocation='http://java.com/jre-install.xpi')
        else:
            plugin.update(
                guid='{fbe640ef-4375-4f45-8d79-767d60bf75b8}')

    elif (g['mimetype'] in ['application/pdf', 'application/vnd.fdf',
                            'application/vnd.adobe.xfdf',
                            'application/vnd.adobe.xdp+xml',
                            'application/vnd.adobe.xfd+xml'] and
          re.match(r'^(Win|PPC Mac OS X|Linux(?! x86_64))', g['clientOS'])):
        plugin.update(
            name='Adobe Acrobat Plug-In',
            guid='{d87cd824-67cb-4547-8587-616c70318095}',
            manualInstallationURL='http://www.adobe.com/products/acrobat/readstep.html')

    elif (g['mimetype'] == 'application/x-mtx' and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='Viewpoint Media Player',
            guid='{03f998b2-0e00-11d3-a498-00104b6eb52e}',
            manualInstallationURL='http://www.viewpoint.com/pub/products/vmp.html')

    elif re.match(wmp_re, g['mimetype']):
        # We serve up the Windows Media Player plugin for the following
        # mimetypes:
        #
        # application/asx
        # application/x-mplayer2
        # audio/x-ms-wax
        # audio/x-ms-wma
        # video/x-ms-asf
        # video/x-ms-asf-plugin
        # video/x-ms-wm
        # video/x-ms-wmp
        # video/x-ms-wmv
        # video/x-ms-wmx
        # video/x-ms-wvx
        #
        # For all windows users who don't have the WMP 11 plugin, give them
        # a link for it.
        if g['clientOS'].startswith('Win'):
            plugin.update(
                name='Windows Media Player',
                version='11',
                guid='{cff1240a-fd24-4b9f-8183-ccd96e5300d0}',
                manualInstallationURL='http://port25.technet.com/pages/windows-media-player-firefox-plugin-download.aspx')

        # For OSX users -- added Intel to this since flip4mac is a UB.
        # Contact at MS was okay w/ this, plus MS points to this anyway.
        elif re.match(r'^(PPC|Intel) Mac OS X', g['clientOS']):
            plugin.update(
                name='Flip4Mac',
                version='2.1',
                guid='{cff0240a-fd24-4b9f-8183-ccd96e5300d0}',
                manualInstallationURL='http://www.flip4mac.com/wmv_download.htm')

    elif (g['mimetype'] == 'application/x-xstandard' and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='XStandard XHTML WYSIWYG Editor',
            guid='{3563d917-2f44-4e05-8769-47e655e92361}',
            iconUrl='http://xstandard.com/images/xicon32x32.gif',
            XPILocation='http://xstandard.com/download/xstandard.xpi',
            InstallerShowsUI='false',
            manualInstallationURL='http://xstandard.com/download/',
            licenseURL='http://xstandard.com/license/')

    elif (g['mimetype'] == 'application/x-dnl' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='DNL Reader',
            guid='{ce9317a3-e2f8-49b9-9b3b-a7fb5ec55161}',
            version='5.5',
            iconUrl='http://digitalwebbooks.com/reader/dwb16.gif',
            XPILocation='http://digitalwebbooks.com/reader/xpinst.xpi',
            InstallerShowsUI='false',
            manualInstallationURL='http://digitalwebbooks.com/reader/')

    elif (g['mimetype'] == 'application/x-videoegg-loader' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='VideoEgg Publisher',
            guid='{b8b881f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://videoegg.com/favicon.ico',
            XPILocation='http://update.videoegg.com/Install/Windows/Initial/VideoEggPublisher.xpi',
            InstallerShowsUI='true',
            manualInstallationURL='http://www.videoegg.com/')

    elif (g['mimetype'] == 'video/divx' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='DivX Web Player',
            guid='{a8b771f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://images.divx.com/divx/player/webplayer.png',
            XPILocation='http://download.divx.com/player/DivXWebPlayer.xpi',
            InstallerShowsUI='false',
            licenseURL='http://go.divx.com/plugin/license/',
            manualInstallationURL='http://go.divx.com/plugin/download/')

    elif (g['mimetype'] == 'video/divx' and
          re.match(r'^(PPC|Intel) Mac OS X', g['clientOS'])):
        plugin.update(
            name='DivX Web Player',
            guid='{a8b771f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://images.divx.com/divx/player/webplayer.png',
            XPILocation='http://download.divx.com/player/DivXWebPlayerMac.xpi',
            InstallerShowsUI='false',
            licenseURL='http://go.divx.com/plugin/license/',
            manualInstallationURL='http://go.divx.com/plugin/download/')

    # End ridiculously huge and embarrassing if-else block.

    return render_xml(request, 'services/plugin_check.xml', {'plugin': plugin})

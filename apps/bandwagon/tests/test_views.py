# -*- coding: utf-8 -*-
import json
import os
import urlparse

from django.conf import settings
import django.test
from django.utils.datastructures import MultiValueDict
from django.utils import encoding

from mock import patch, Mock
from nose.tools import eq_
import path
from pyquery import PyQuery as pq

import amo
import amo.tests
from addons.models import Addon
from addons.tests.test_views import TestMobile
from amo.urlresolvers import reverse
from amo.utils import urlparams
from amo.tests.test_helpers import get_uploaded_file
from bandwagon import forms
from bandwagon.models import (Collection, CollectionVote, CollectionUser,
                              CollectionWatcher)
from bandwagon.views import CollectionFilter
from browse.tests import TestFeeds
from devhub.models import ActivityLog
from users.models import UserProfile


def test_addons_form():
    f = forms.AddonsForm(MultiValueDict({'addon': [''],
                                         'addon_comment': ['comment']}))
    eq_(f.is_valid(), True)


def test_collections_form_bad_slug():
    f = forms.CollectionForm(dict(slug=' ', listed=True, name='  '))
    assert 'slug' in f.errors
    assert 'name' in f.errors


def test_collections_form_long_description():
    f = forms.CollectionForm(dict(description='&*' * 200))
    assert 'description' in f.errors


def test_collections_form_unicode_slug():
    u = Mock()
    u.collections.filter.return_value.count.return_value = False
    f = forms.CollectionForm(dict(slug=u'Ελλην', listed=True, name='  '),
                             initial=dict(author=u))
    assert 'name' in f.errors
    assert 'slug' not in f.errors


class HappyUnicodeClient(django.test.Client):
    """
    Django's test client runs urllib.unquote on the path you pass in.

    urlilib.unquote does not understand unicode.  It's going to ruin your life.

    >>> u =  u'\u05d0\u05d5\u05e1\u05e3'
    >>> encoding.iri_to_uri(u)
    '%D7%90%D7%95%D7%A1%D7%A3'
    >>> urllib.unquote(encoding.iri_to_uri(u))
    '\xd7\x90\xd7\x95\xd7\xa1\xd7\xa3'
    >>> urllib.unquote(unicode(encoding.iri_to_uri(u)))
    u'\xd7\x90\xd7\x95\xd7\xa1\xd7\xa3'
    >>> _ == __
    False

    It all looks the same but the u on the front of the second string means
    it's interpreted completely different.  I don't even know.
    """

    def get(self, path_, *args, **kw):
        path_ = encoding.smart_str(path_)
        return super(HappyUnicodeClient, self).get(path_, *args, **kw)

    def post(self, path_, *args, **kw):
        path_ = encoding.smart_str(path_)
        return super(HappyUnicodeClient, self).post(path_, *args, **kw)

    # Add head, put, options, delete if you need them.


class TestViews(amo.tests.TestCase):
    fixtures = ['users/test_backends', 'bandwagon/test_models',
                'base/addon_3615']

    def check_response(self, url, code, to=None):
        response = self.client.get(url, follow=True)
        if code == 404:
            eq_(response.status_code, 404)
        elif code in (301, 302):
            self.assertRedirects(response, to, status_code=code)
        else:  # pragma: no cover
            assert code in (301, 302, 404), code

    def test_legacy_redirects(self):
        collection = Collection.objects.get(nickname='wut')
        url = collection.get_url_path()
        tests = [
            ('/collection/wut?x=y', 301, url + '?x=y'),
            ('/collection/wut/', 301, url),
            ('/collection/f94d08c7-794d-3ce4-4634-99caa09f9ef4', 301, url),
            ('/collection/f94d08c7-794d-3ce4-4634-99caa09f9ef4/', 301, url),
            ('/collections/view/f94d08c7-794d-3ce4-4634-99caa09f9ef4', 301,
             url),
            ('/collections/view/wut/', 301, url),
            ('/collection/404', 404)]
        for test in tests:
            self.check_response(*test)

    def test_legacy_redirects_edit(self):
        self.client.login(username='jbalogh@mozilla.com', password='foo')
        u = UserProfile.objects.get(email='jbalogh@mozilla.com')
        uuid = u.favorites_collection().uuid
        self.check_response('/collections/edit/%s' % uuid, 301,
                            u.favorites_collection().edit_url())

    def test_collection_directory_redirects(self):
        base = reverse('collections.list')
        tests = [
            ('/collections/editors_picks', 301,
             urlparams(base, sort='featured')),
            ('/collections/popular/', 301,
             urlparams(base, sort='popular')),
            # These don't work without a login.
            ('/collections/favorites/', 301, base),
        ]
        for test in tests:
            self.check_response(*test)

    def test_collection_directory_redirects_with_login(self):
        self.client.login(username='jbalogh@mozilla.com', password='foo')

        tests = [
            ('/collections/mine', 301,
             reverse('collections.user', args=['jbalogh'])),
            ('/collections/favorites/', 301,
             reverse('collections.following')),
        ]
        for test in tests:
            self.check_response(*test)

    def test_unreviewed_addon(self):
        u = UserProfile.objects.get(email='jbalogh@mozilla.com')
        addon = Addon.objects.all()[0]
        addon.status = amo.STATUS_UNREVIEWED
        c = u.favorites_collection()
        amo.set_user(u)
        c.add_addon(addon)

        self.client.login(username='jbalogh@mozilla.com', password='foo')
        response = self.client.get(c.get_url_path())
        eq_(list(response.context['addons'].object_list), [addon])


class TestPrivacy(amo.tests.TestCase):
    fixtures = ['users/test_backends']

    def setUp(self):
        # The favorites collection is created automatically.
        self.url = reverse('collections.detail', args=['jbalogh', 'favorites'])
        self.client.login(username='jbalogh@mozilla.com', password='foo')
        eq_(self.client.get(self.url).status_code, 200)
        self.client.logout()
        self.c = Collection.objects.get(slug='favorites',
                                        author__username='jbalogh')

    def test_owner(self):
        self.client.login(username='jbalogh@mozilla.com', password='foo')
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        # TODO(cvan): Uncomment when bug 719512 gets fixed.
        #eq_(pq(r.content)('.meta .view-stats').length, 1,
        #    'Add-on authors should be able to view stats')

    def test_private(self):
        self.client.logout()
        self.client.login(username='fligtar@gmail.com', password='foo')
        eq_(self.client.get(self.url).status_code, 403)

    def test_public(self):
        # Make it public, others can see it.
        eq_(self.client.get(self.url).status_code, 403)
        self.c.listed = True
        self.c.save()
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        eq_(pq(r.content)('.meta .view-stats').length, 0,
            'Only add-on authors can view stats')

    def test_publisher(self):
        self.c.listed = False
        self.c.save()
        eq_(self.client.get(self.url).status_code, 403)
        u = UserProfile.objects.get(email='fligtar@gmail.com')
        CollectionUser.objects.create(collection=self.c, user=u)
        self.client.login(username='fligtar@gmail.com', password='foo')
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        # TODO(cvan): Uncomment when bug 719512 gets fixed.
        #eq_(pq(r.content)('.meta .view-stats').length, 1,
        #    'Add-on authors (not just owners) should be able to view stats')


class TestVotes(amo.tests.TestCase):
    fixtures = ['users/test_backends']

    def setUp(self):
        self.client.login(username='jbalogh@mozilla.com', password='foo')
        args = ['fligtar', 'slug']
        Collection.objects.create(slug='slug', author_id=9945)
        self.c_url = reverse('collections.detail', args=args)
        self.up = reverse('collections.vote', args=args + ['up'])
        self.down = reverse('collections.vote', args=args + ['down'])

    def test_login_required(self):
        self.client.logout()
        r = self.client.post(self.up, follow=True)
        url, _ = r.redirect_chain[-1]
        eq_(r.status_code, 200)
        self.assert_(reverse('users.login') in url)

    def test_post_required(self):
        r = self.client.get(self.up, follow=True)
        self.assertRedirects(r, self.c_url)

    def check(self, upvotes=0, downvotes=0):
        c = Collection.uncached.get(slug='slug', author=9945)
        eq_(c.upvotes, upvotes)
        eq_(c.downvotes, downvotes)
        eq_(CollectionVote.objects.filter(user=4043307, vote=1).count(),
            upvotes)
        eq_(CollectionVote.objects.filter(user=4043307, vote=-1).count(),
            downvotes)
        eq_(CollectionVote.objects.filter(user=4043307).count(),
            upvotes + downvotes)

    def test_upvote(self):
        self.client.post(self.up)
        self.check(upvotes=1)

    def test_downvote(self):
        self.client.post(self.down)
        self.check(downvotes=1)

    def test_down_then_up(self):
        self.client.post(self.down)
        self.check(downvotes=1)
        self.client.post(self.up)
        self.check(upvotes=1)

    def test_up_then_up(self):
        self.client.post(self.up)
        self.check(upvotes=1)
        self.client.post(self.up)
        self.check(upvotes=0)

    def test_normal_response(self):
        r = self.client.post(self.up, follow=True)
        self.assertRedirects(r, self.c_url)

    def test_ajax_response(self):
        r = self.client.post_ajax(self.up, follow=True)
        assert not r.redirect_chain
        eq_(r.status_code, 200)


class TestCRUD(amo.tests.TestCase):
    """Test the collection form."""
    fixtures = ('base/apps',
                'base/users',
                'base/addon_3615',
                'base/collections',
               )

    def setUp(self):
        self.client = HappyUnicodeClient()
        self.add_url = reverse('collections.add')
        self.login_admin()
        # Oh god it's unicode.
        self.slug = u'\u05d0\u05d5\u05e1\u05e3'
        self.data = {
                'addon': 3615,
                'addon_comment': 'fff',
                'name': u'קווים תחתונים ומקפים בלבד',
                'slug': self.slug,
                'description': '',
                'listed': 'True',
                }

    def login_admin(self):
        assert self.client.login(username='admin@mozilla.com',
                                 password='password')

    def login_regular(self):
        assert self.client.login(username='regular@mozilla.com',
                                  password='password')

    def create_collection(self, **kw):
        self.data.update(kw)
        r = self.client.post(self.add_url, self.data, follow=True)
        eq_(r.status_code, 200)
        return r

    def test_listing_xss(self):
        c = Collection.objects.get(id=80)
        assert self.client.login(username='clouserw@gmail.com',
                                 password='password')

        url = reverse('collections.watch', args=[c.author.username, c.slug])

        user = UserProfile.objects.get(id='10482')
        user.display_name = "<script>alert(1)</script>"
        user.save()

        r = self.client.post(url, follow=True)
        eq_(r.status_code, 200)

        qs = CollectionWatcher.objects.filter(user__username='clouserw',
                                              collection=80)
        eq_(qs.count(), 1)

        r = self.client.get('/en-US/firefox/collections/following/',
                            follow=True)

        assert '&lt;script&gt;alert' in r.content
        assert '<script>alert' not in r.content

    def test_add_fail(self):
        """
        If we input addons but fail at filling out the form, don't show
        invisible addons.
        """
        data = {
                'addon': 3615,
                'addon_comment': 'fff',
                'description': '',
                'listed': 'True',
                }

        r = self.client.post(self.add_url, data, follow=True)
        eq_(pq(r.content)('.errorlist li')[0].text, 'This field is required.')
        self.assertContains(r, 'Delicious')

    def test_default_locale(self):
        r = self.client.post('/he/firefox/collections/add',
                             self.data, follow=True)
        eq_(r.status_code, 200)
        c = Collection.objects.get(slug=self.slug)
        eq_(c.default_locale, 'he')

    def test_fix_slug(self):
        self.data['slug'] = 'some slug'
        self.create_collection()
        Collection.objects.get(slug='some-slug')

    def test_showform(self):
        """Shows form if logged in."""
        r = self.client.get(self.add_url)
        eq_(r.status_code, 200)

    def test_breadcrumbs(self):
        r = self.client.get(self.add_url)
        expected = [
            ('Add-ons for Firefox', reverse('home')),
            ('Collections', reverse('collections.list')),
            ('Create', None)
        ]
        amo.tests.check_links(expected, pq(r.content)('#breadcrumbs li'))

    def test_submit(self):
        """Test submission of addons."""
        # TODO(davedash): Test file uploads, test multiple addons.
        r = self.client.post(self.add_url, self.data, follow=True)
        eq_(r.request['PATH_INFO'].decode('utf-8'),
            '/en-US/firefox/collections/admin/%s/' % self.slug)
        c = Collection.objects.get(slug=self.slug)
        eq_(unicode(c.name), self.data['name'])
        eq_(c.description, '')
        eq_(c.addons.all()[0].id, 3615)

    def test_duplicate_slug(self):
        """Try the same thing twice.  AND FAIL"""
        self.client.post(self.add_url, self.data, follow=True)
        r = self.client.post(self.add_url, self.data, follow=True)
        eq_(r.context['form'].errors['slug'][0],
            'This url is already in use by another collection')

    def test_reassign(self):
        """
        When reassigning an addon make sure we don't give it a duplicate slug.
        """

        # Create an addon by user 1.
        self.create_collection()

        # Create an addon by user 2 with matching slug.
        self.login_regular()
        self.client.post(self.add_url, self.data, follow=True)
        # Add user1 to user 2.

        # Make user1 owner of user2s addon.
        url = reverse('collections.edit_contributors',
                      args=['regularuser', self.slug])
        r = self.client.post(url,
                             {'contributor': 4043307, 'new_owner': 4043307},
                             follow=True)
        eq_(r.status_code, 200)
        # verify that user1's addon is slug + '-'
        c = Collection.objects.get(slug=self.slug)
        eq_(c.author_id, 4043307)

    def test_edit(self):
        self.create_collection()
        url = reverse('collections.edit', args=['admin', self.slug])
        r = self.client.get(url, follow=True)
        eq_(r.status_code, 200)

    def test_edit_contributors_form(self):
        self.create_collection()
        url = reverse('collections.edit', args=['admin', self.slug])
        r = self.client.get(url, follow=True)
        eq_(Collection.objects.get(slug=self.slug).author_id,
            long(pq(r.content)('#contributor-ac').attr('data-owner')))

    def test_edit_breadcrumbs(self):
        c = Collection.objects.all()[0]
        r = self.client.get(reverse('collections.edit',
                                    args=[c.author.username, c.slug]))
        links = pq(r.content.decode('utf-8'))('#breadcrumbs li')
        expected = [
            ('Add-ons for Firefox', reverse('home')),
            ('Collections', reverse('collections.list')),
            (c.author.name, reverse('collections.user',
                                    args=[c.author.username])),
            (c.name, reverse('collections.detail',
                             args=[c.author.username, c.slug])),
            ('Edit', None),
        ]
        amo.tests.check_links(expected, links)

    def test_edit_post(self):
        """Test edit of collection."""
        self.create_collection()
        url = reverse('collections.edit', args=['admin', self.slug])

        r = self.client.post(url, {'name': 'HALP', 'slug': 'halp',
                                   'listed': True}, follow=True)
        eq_(r.status_code, 200)
        c = Collection.objects.get(slug='halp')
        eq_(unicode(c.name), 'HALP')

    def test_edit_description(self):
        self.create_collection()

        url = reverse('collections.edit', args=['admin', self.slug])
        self.data['description'] = 'abc'
        edit_url = Collection.objects.get(slug=self.slug).edit_url()
        r = self.client.post(url, self.data)
        self.assertRedirects(r, edit_url, 302)
        eq_(unicode(Collection.objects.get(slug=self.slug).description),
            'abc')

    def test_edit_no_description(self):
        self.create_collection(description='abc')
        eq_(Collection.objects.get(slug=self.slug).description, 'abc')

        url = reverse('collections.edit', args=['admin', self.slug])
        self.data['description'] = ''
        edit_url = Collection.objects.get(slug=self.slug).edit_url()
        r = self.client.post(url, self.data)
        self.assertRedirects(r, edit_url, 302)
        eq_(unicode(Collection.objects.get(slug=self.slug).description),
            '')

    def test_edit_spaces(self):
        """Let's put lots of spaces and see if they show up."""
        self.create_collection()
        url = reverse('collections.edit', args=['admin', self.slug])

        r = self.client.post(url,
                             {'name': '  H A L  P ', 'slug': '  halp  ',
                              'listed': True}, follow=True)
        eq_(r.status_code, 200)
        c = Collection.objects.get(slug='halp')
        eq_(unicode(c.name), 'H A L  P')

    def test_forbidden_edit(self):
        r = self.client.post(self.add_url, self.data, follow=True)
        self.login_regular()
        url_args = ['admin', self.slug]

        url = reverse('collections.edit', args=url_args)
        r = self.client.get(url)
        eq_(r.status_code, 403)

        url = reverse('collections.edit_addons', args=url_args)
        r = self.client.get(url)
        eq_(r.status_code, 403)

        url = reverse('collections.edit_contributors', args=url_args)
        r = self.client.get(url)
        eq_(r.status_code, 403)

    def test_edit_favorites(self):
        r = self.client.get(reverse('collections.list'))
        fav = r.context['request'].amo_user.favorites_collection()
        r = self.client.post(fav.edit_url(), {'name': 'xxx', 'listed': True})
        eq_(r.status_code, 302)

        c = Collection.objects.get(id=fav.id)
        eq_(unicode(c.name), 'xxx')

    def test_edit_contrib_tab(self):
        self.create_collection()
        url = reverse('collections.edit', args=['admin', self.slug])
        r = self.client.get(url)
        doc = pq(r.content)
        eq_(doc('.tab-nav li a[href$=users-edit]').length, 1)
        eq_(doc('#users-edit').length, 1)

    def test_edit_contrib_success_message(self):
        self.create_collection()
        url = reverse('collections.edit_contributors',
                      args=['admin', self.slug])
        r = self.client.post(url, {'contributor': 999,
                                   'application_id': 1,
                                   'type': 1},
                             follow=True)
        doc = pq(r.content)('.success')
        eq_(doc('h2').text(), 'Collection updated!')
        eq_(doc('p').text(), 'View your collection to see the changes.')

    def test_edit_no_contrib_tab(self):
        self.create_collection()
        c = Collection.uncached.get(slug=self.slug)
        url = c.edit_url()

        c.update(type=amo.COLLECTION_FAVORITES)
        r = self.client.get(url)
        doc = pq(r.content)
        eq_(doc('.tab-nav li a[href$=users-edit]').length, 0)
        eq_(doc('#users-edit').length, 0)

        c.update(type=amo.COLLECTION_SYNCHRONIZED)
        r = self.client.get(url)
        doc = pq(r.content)
        eq_(doc('.tab-nav li a[href$=users-edit]').length, 0)
        eq_(doc('#users-edit').length, 0)

    def test_edit_addons_get(self):
        self.create_collection()
        url = reverse('collections.edit_addons', args=['admin', self.slug])
        r = self.client.get(url, follow=True)
        eq_(r.status_code, 405)

    def test_edit_addons_post(self):
        self.create_collection()
        url = reverse('collections.edit_addons',
                      args=['admin', self.slug])
        r = self.client.post(url, {'addon': 3615}, follow=True)
        addon = Collection.objects.filter(slug=self.slug)[0].addons.all()[0]
        eq_(addon.id, 3615)
        doc = pq(r.content)('.success')
        eq_(doc('h2').text(), 'Collection updated!')
        eq_(doc('p').text(), 'View your collection to see the changes.')

    def test_delete(self):
        self.create_collection()
        eq_(len(Collection.objects.filter(slug=self.slug)), 1)

        url = reverse('collections.delete',
                      args=['admin', self.slug])
        self.client.post(url, dict(sure=0))
        eq_(len(Collection.objects.filter(slug=self.slug)), 1)
        self.client.post(url, dict(sure='1'))
        eq_(len(Collection.objects.filter(slug=self.slug)), 0)

    def test_delete_breadcrumbs(self):
        c = Collection.objects.all()[0]
        r = self.client.get(reverse('collections.delete',
                                    args=[c.author.username, c.slug]))
        links = pq(r.content.decode('utf-8'))('#breadcrumbs li')
        expected = [
            ('Add-ons for Firefox', reverse('home')),
            ('Collections', reverse('collections.list')),
            (c.author.name, reverse('collections.user',
                                    args=[c.author.username])),
            (c.name, reverse('collections.detail',
                             args=[c.author.username, c.slug])),
            ('Delete', None),
        ]
        amo.tests.check_links(expected, links)

    @patch('access.acl.action_allowed')
    def test_admin(self, f):
        self.create_collection()
        url = reverse('collections.edit',
                      args=['admin', self.slug])
        r = self.client.get(url, follow=True)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        assert 'Admin Settings' in doc('form h3').text()

        r = self.client.post(url, dict(application=1, type=0), follow=True)
        eq_(r.status_code, 200)

    def test_delete_link(self):
         # Create an addon by user 1.
        self.create_collection()

        url = reverse('collections.edit_contributors',
                      args=['admin', self.slug])
        self.client.post(url,
                         {'contributor': 999, 'application_id': 1, 'type': 1},
                         follow=True)
        url = reverse('collections.edit', args=['admin', self.slug])

        r = self.client.get(url)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(len(doc('a.delete')), 2)

        # TODO: bug 590305
        return
        self.login_regular()
        r = self.client.get(url)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(len(doc('a.delete')), 0)

    def test_form_uneditable_slug(self):
        """
        Editing a mobile or favorite collection should have an uneditable slug.
        """
        u = UserProfile.objects.get(username='admin')
        Collection(author=u, slug='mobile', type=amo.COLLECTION_MOBILE).save()
        url = reverse('collections.edit', args=['admin', 'mobile'])
        r = self.client.get(url, follow=True)
        doc = pq(r.content)
        eq_(len(doc('#id_slug')), 0)

    def test_form_uneditable_slug_submit(self):
        """
        Ignore the slug request change, if some jackass thinks he can change
        it.
        """
        u = UserProfile.objects.get(username='admin')
        Collection(author=u, slug='mobile', type=amo.COLLECTION_MOBILE).save()
        url = reverse('collections.edit', args=['admin', 'mobile'])
        self.client.post(url, {'name': 'HALP', 'slug': 'halp', 'listed': True},
                         follow=True)

        assert not Collection.objects.filter(slug='halp', author=u)
        assert Collection.objects.filter(slug='mobile', author=u)

    def test_no_changing_owners(self):
        self.login_regular()
        self.create_collection()
        c = Collection.objects.get(slug=self.slug)

        self.login_admin()
        r = self.client.post(c.edit_url(),
                             dict(name='new name', slug=self.slug,
                                  listed=True),
                             follow=True)
        eq_(r.status_code, 200)

        newc = Collection.objects.get(slug=self.slug,
                                      author__username=c.author_username)
        eq_(unicode(newc.name), 'new name')


class TestChangeAddon(amo.tests.TestCase):
    fixtures = ['users/test_backends']

    def setUp(self):
        self.client.login(username='jbalogh@mozilla.com', password='foo')
        self.add = reverse('collections.alter',
                           args=['jbalogh', 'mobile', 'add'])
        self.remove = reverse('collections.alter',
                              args=['jbalogh', 'mobile', 'remove'])
        self.flig = Collection.objects.create(author_id=9945, slug='xxx')
        self.flig_add = reverse('collections.alter',
                                args=['fligtar', 'xxx', 'add'])
        self.addon = Addon.objects.create(type=amo.ADDON_EXTENSION)

    def check_redirect(self, request):
        url = '%s?addon_id=%s' % (reverse('collections.ajax_list'),
                                  self.addon.id)
        self.assertRedirects(request, url)

    def test_login_required(self):
        self.client.logout()
        r = self.client.post(self.add)
        eq_(r.status_code, 302)
        self.assert_(reverse('users.login') in r['Location'], r['Location'])

    def test_post_required(self):
        r = self.client.get(self.add)
        eq_(r.status_code, 405)

    def test_ownership(self):
        r = self.client.post(self.flig_add)
        eq_(r.status_code, 403)

    def test_publisher(self):
        CollectionUser.objects.create(user_id=4043307, collection=self.flig)
        r = self.client.post_ajax(self.flig_add, {'addon_id': self.addon.id})
        self.check_redirect(r)

    def test_no_addon(self):
        r = self.client.post(self.add)
        eq_(r.status_code, 400)

    def test_add_success(self):
        r = self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        self.check_redirect(r)
        c = Collection.objects.get(author__username='jbalogh', slug='mobile')
        self.assert_(self.addon in c.addons.all())
        eq_(c.addons.count(), 1)

    def test_add_secretly(self):
        """
        When we add to a private collection, make sure we don't log anything.
        """
        self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        # There should be no log objects for this add-on
        eq_(len(ActivityLog.objects.for_addons(self.addon)), 0)

    def test_add_existing(self):
        r = self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        self.check_redirect(r)
        r = self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        self.check_redirect(r)
        c = Collection.objects.get(author__username='jbalogh', slug='mobile')
        self.assert_(self.addon in c.addons.all())
        eq_(c.addons.count(), 1)

    def test_remove_secretly(self):
        """
        When we remove from a private collection, make sure we don't log
        anything.
        """
        self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        self.client.post_ajax(self.remove, {'addon_id': self.addon.id})
        # There should be no log objects for this add-on
        eq_(len(ActivityLog.objects.for_addons(self.addon)), 0)

    def test_remove_success(self):
        r = self.client.post_ajax(self.add, {'addon_id': self.addon.id})
        self.check_redirect(r)

        r = self.client.post_ajax(self.remove, {'addon_id': self.addon.id})
        self.check_redirect(r)

        c = Collection.objects.get(author__username='jbalogh', slug='mobile')
        eq_(c.addons.count(), 0)

    def test_remove_nonexistent(self):
        r = self.client.post_ajax(self.remove, {'addon_id': self.addon.id})
        self.check_redirect(r)
        c = Collection.objects.get(author__username='jbalogh', slug='mobile')
        eq_(c.addons.count(), 0)

    def test_no_ajax_response(self):
        r = self.client.post(self.add, {'addon_id': self.addon.id},
                             follow=True)
        self.assertRedirects(r, reverse('collections.detail',
                                        args=['jbalogh', 'mobile']))


class AjaxTest(amo.tests.TestCase):
    fixtures = ('base/apps', 'base/users', 'base/addon_3615',
                'base/addon_5299_gcal', 'base/collections')

    def setUp(self):
        assert self.client.login(username='clouserw@gmail.com',
                                 password='password')
        self.user = UserProfile.objects.get(email='clouserw@gmail.com')
        self.other = UserProfile.objects.exclude(id=self.user.id)[0]

    def test_list_collections(self):
        r = self.client.get(reverse('collections.ajax_list')
                            + '?addon_id=3615',)
        doc = pq(r.content)
        eq_(doc('li').attr('data-id'), '80')

    def test_add_collection(self):
        r = self.client.post_ajax(reverse('collections.ajax_add'),
                                  {'addon_id': 3615, 'id': 80}, follow=True)
        doc = pq(r.content)
        eq_(doc('li.selected').attr('data-id'), '80')

    def test_bad_collection(self):
        r = self.client.post(reverse('collections.ajax_add'), {'id': 'adfa'})
        eq_(r.status_code, 400)

    def test_remove_collection(self):
        r = self.client.post(reverse('collections.ajax_remove'),
                             {'addon_id': 1843, 'id': 80}, follow=True)
        doc = pq(r.content)
        eq_(len(doc('li.selected')), 0)

    def test_new_collection(self):
        num_collections = Collection.objects.all().count()
        r = self.client.post(reverse('collections.ajax_new'),
                {'addon_id': 5299,
                 'name': 'foo',
                 'slug': 'auniqueone',
                 'description': 'yermom',
                 'listed': True},
                follow=True)
        doc = pq(r.content)
        eq_(len(doc('li.selected')), 1, "The new collection is not selected.")
        eq_(Collection.objects.all().count(), num_collections + 1)

    def test_add_other_collection(self):
        "403 when you try to add to a collection that isn't yours."
        c = Collection(author=self.other)
        c.save()

        r = self.client.post(reverse('collections.ajax_add'),
                             {'addon_id': 3615, 'id': c.id}, follow=True)
        eq_(r.status_code, 403)

    def test_remove_other_collection(self):
        "403 when you try to add to a collection that isn't yours."
        c = Collection(author=self.other)
        c.save()

        r = self.client.post(reverse('collections.ajax_remove'),
                             {'addon_id': 3615, 'id': c.id}, follow=True)
        eq_(r.status_code, 403)

    def test_ajax_list_no_addon_id(self):
        eq_(self.client.get(reverse('collections.ajax_list')).status_code, 400)

    def test_ajax_list_bad_addon_id(self):
        url = reverse('collections.ajax_list') + '?addon_id=fff'
        eq_(self.client.get(url).status_code, 400)


class TestWatching(amo.tests.TestCase):
    fixtures = ['base/users', 'base/collection_57181']

    def setUp(self):
        self.collection = c = Collection.objects.get(id=57181)
        self.url = reverse('collections.watch',
                           args=[c.author.username, c.slug])
        assert self.client.login(username='clouserw@gmail.com',
                                 password='password')

        self.qs = CollectionWatcher.objects.filter(user__username='clouserw',
                                                   collection=57181)
        eq_(self.qs.count(), 0)

    def test_watch(self):
        r = self.client.post(self.url, follow=True)
        eq_(r.status_code, 200)
        eq_(self.qs.count(), 1)

    def test_unwatch(self):
        r = self.client.post(self.url, follow=True)
        eq_(r.status_code, 200)
        r = self.client.post(self.url, follow=True)
        eq_(r.status_code, 200)
        eq_(self.qs.count(), 0)

    def test_amouser_watching(self):
        r = self.client.post(self.url, follow=True)
        eq_(r.status_code, 200)
        r = self.client.get('/en-US/firefox/')
        eq_(r.context['amo_user'].watching, [57181])

    def test_ajax_response(self):
        r = self.client.post_ajax(self.url, follow=True)
        eq_(r.status_code, 200)
        eq_(json.loads(r.content), {'watching': True})


class TestSharing(amo.tests.TestCase):
    fixtures = ['base/collection_57181']

    def test_twitter_share(self):
        c = Collection.objects.get(id=57181)
        r = self.client.get(c.share_url() + '?service=twitter')
        eq_(r.status_code, 302)
        loc = urlparse.urlparse(r['Location'])
        query = dict(urlparse.parse_qsl(loc.query))
        eq_(loc.netloc, 'twitter.com')
        status = 'Home Business Auto :: Add-ons for Firefox'
        assert status in query['status'], query['status']

    def test_404(self):
        c = Collection.objects.get(id=57181)
        url = reverse('collections.share', args=[c.author.username, c.slug])
        r = self.client.get(url)
        eq_(r.status_code, 404)
        r = self.client.get(url + '?service=xxx')
        eq_(r.status_code, 404)


class TestCollectionFeed(TestFeeds):
    fixtures = TestFeeds.fixtures

    def setUp(self):
        super(TestCollectionFeed, self).setUp()
        self.url = reverse('collections.list')
        self.rss_url = reverse('collections.rss')
        self.filter = CollectionFilter


class TestCollectionListing(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/category', 'base/featured',
                'addons/featured', 'addons/listed', 'base/collections',
                'bandwagon/featured_collections']

    def setUp(self):
        self.reset_featured_addons()
        self.url = reverse('collections.list')

    def test_default_sort(self):
        r = self.client.get(self.url)
        eq_(r.context['sorting'], 'featured')

    def test_featured_sort(self):
        r = self.client.get(urlparams(self.url, sort='featured'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'opt')
        eq_(sel.text(), 'Featured')

    def test_users_redirect(self):
        """Test that 'users' sort redirects to 'followers' sort."""
        r = self.client.get(urlparams(self.url, sort='users'))
        self.assertRedirects(r, urlparams(self.url, sort='followers'), 301)

    def test_mostsubscribers_sort(self):
        r = self.client.get(urlparams(self.url, sort='followers'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'opt')
        eq_(sel.text(), 'Most Followers')
        c = r.context['collections'].object_list
        eq_(list(c), sorted(c, key=lambda x: x.subscribers, reverse=True))

    def test_newest_sort(self):
        r = self.client.get(urlparams(self.url, sort='created'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'opt')
        eq_(sel.text(), 'Newest')
        c = r.context['collections'].object_list
        eq_(list(c), sorted(c, key=lambda x: x.created, reverse=True))

    def test_name_sort(self):
        r = self.client.get(urlparams(self.url, sort='name'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'extra-opt')
        eq_(sel.text(), 'Name')
        c = r.context['collections'].object_list
        eq_(list(c), sorted(c, key=lambda x: x.name))

    def test_updated_sort(self):
        r = self.client.get(urlparams(self.url, sort='updated'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'extra-opt')
        eq_(sel.text(), 'Recently Updated')
        c = r.context['collections'].object_list
        eq_(list(c), sorted(c, key=lambda x: x.modified, reverse=True))

    def test_popular_sort(self):
        r = self.client.get(urlparams(self.url, sort='popular'))
        sel = pq(r.content)('#sorter ul > li.selected')
        eq_(sel.find('a').attr('class'), 'extra-opt')
        eq_(sel.text(), 'Recently Popular')
        c = r.context['collections'].object_list
        eq_(list(c),
            sorted(c, key=lambda x: x.weekly_subscribers, reverse=True))

    def test_added_date(self):
        doc = pq(self.client.get(urlparams(self.url, sort='created')).content)
        eq_(doc('.items .item .updated').text().startswith('Added'), True)

    def test_updated_date(self):
        d = pq(self.client.get(urlparams(self.url, sort='updated')).content)
        eq_(d('.items .item .updated').text().startswith('Updated'), True)

    def test_mostsubscribers_adu_unit(self):
        d = pq(self.client.get(urlparams(self.url, sort='followers')).content)
        eq_('follower' in d('.items .item .followers').text(), True)
        eq_('weekly follower' in d('.items .item .followers').text(), False)

    def test_popular_adu_unit(self):
        d = pq(self.client.get(urlparams(self.url, sort='popular')).content)
        eq_('weekly follower' in d('.items .item .followers').text(), True)


class TestCollectionDetailFeed(amo.tests.TestCase):
    fixtures = ['base/collection_57181']

    def setUp(self):
        self.collection = c = Collection.objects.get(id=57181)
        self.feed_url = reverse('collections.detail.rss',
                                args=[c.author.username, c.slug])

    def test_collection_feed(self):
        eq_(self.client.get(self.feed_url).status_code, 200)

    def test_feed_redirect(self):
        r = self.client.get(self.collection.get_url_path() + '?format=rss')
        eq_(r.status_code, 301)
        loc = r['Location']
        assert loc.endswith(self.feed_url), loc

    def test_private_collection(self):
        self.collection.update(listed=False)
        eq_(self.client.get(self.feed_url).status_code, 404)


class TestMobileCollections(TestMobile):

    # for now we want collections disabled.
    def test_collections(self):
        r = self.client.get(reverse('collections.list'))
        eq_(r.status_code, 200)
        self.assertTemplateUsed(r, 'bandwagon/impala/collection_listing.html')


class TestMine(amo.tests.TestCase):
    fixtures = ['base/users']

    def setUp(self):
        assert self.client.login(username='admin@mozilla.com',
                                 password='password')

    def test_mine(self):
        r = self.client.get(reverse('collections.mine'), follow=True)
        self.assertRedirects(r, reverse('collections.user', args=['admin']))

    def test_favorites(self):
        r = self.client.get(reverse('collections.mine', args=['favorites']),
                            follow=True)
        expected = reverse('collections.detail', args=['admin', 'favorites'])
        self.assertRedirects(r, expected)


class TestCollectionForm(amo.tests.TestCase):
    fixtures = ['base/collection_57181']

    @patch('amo.models.ModelBase.update')
    def test_icon(self, update_mock):
        collection = Collection.objects.get(pk=57181)
        # TODO(andym): altering this form is too complicated, can we simplify?
        form = forms.CollectionForm(
                        {'listed': collection.listed,
                         'slug': collection.slug,
                         'name': collection.name},
                        instance=collection,
                        files={'icon': get_uploaded_file('transparent.png')},
                        initial={'author': collection.author,
                                 'application_id': collection.application.pk})
        dest = (path.path(settings.COLLECTIONS_ICON_PATH) / 'uploads' /
                          'collection_icons' / '57')
        if not os.path.exists(dest):
            os.makedirs(dest)
        assert form.is_valid()
        form.save()
        assert update_mock.called

function initPromos($context, module_context, version, platform) {
    if (!$context) {
        $context = $(document.body);
    }
    var $promos = $('#promos[data-promo-url]', $context);
    if (!$promos.length) {
        return;
    }
    var promo_url = $promos.attr('data-promo-url');
    if (!version) {
        version = z.browserVersion;
    }
    if (!platform) {
        platform = z.platform;
    }
    if (z.badBrowser && !version && !platform) {
        version = '5.0';
        platform = 'mac';
    }
    var data = {};
    if (module_context != 'discovery') {
        // The version + platform are passed in the `promo_url` for the
        // discopane promos because when we serve static assets the
        // `?build=<BUILD_ID>` cachebustage kills our querystring.
        data = {version: version, platform: platform};
    }
    $.get(promo_url, data, function(resp) {
        $('.slider', $promos).append($(resp));
        if ($('.panel', $promos).length) {
            // Show promo module only if we have at least panel.
            $promos.trigger('promos_shown', [$promos]);
        }
    });
}

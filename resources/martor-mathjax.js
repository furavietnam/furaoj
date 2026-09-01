jQuery(function ($) {
    $(document).on('martor:preview', function (e, $content) {
        // Pick the right MathJax typesetting call depending on which version
        // is loaded and whether startup has finished. The preview fragment is
        // rendered server-side and may arrive before MathJax v3 has finished
        // initializing (in which case MathJax.typesetPromise does not yet
        // exist on the global MathJax object), or the host page may be
        // running the legacy MathJax v2 which uses MathJax.Hub.Queue.
        function run_typeset(element) {
            if (!element) return;
            if (window.MathJax && typeof window.MathJax.typesetPromise === 'function') {
                // MathJax v3, startup already complete.
                window.MathJax.typesetPromise([element]).then(function () {
                    $content.find('.tex-image').hide();
                    $content.find('.tex-text').show();
                });
            } else if (window.MathJax && window.MathJax.Hub && window.MathJax.Hub.Queue) {
                // MathJax v2 fallback.
                window.MathJax.Hub.Queue(['Typeset', window.MathJax.Hub, element]);
            } else if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise
                       && typeof window.MathJax.startup.promise.then === 'function') {
                // MathJax v3 is still initializing; wait for startup, then typeset.
                window.MathJax.startup.promise.then(function () {
                    if (typeof window.MathJax.typesetPromise === 'function') {
                        window.MathJax.typesetPromise([element]).then(function () {
                            $content.find('.tex-image').hide();
                            $content.find('.tex-text').show();
                        });
                    }
                });
            }
        }

        function update_math() {
            run_typeset($content[0]);
        }

        var $jax = $content.find('.require-mathjax-support');
        if ($jax.length) {
            if (!('MathJax' in window)) {
                $.ajax({
                    type: 'GET',
                    url: $jax.attr('data-config'),
                    dataType: 'script',
                    cache: true,
                    success: function () {
                        // Only set startup.typeset if startup hasn't been
                        // initialized yet. Clobbering an already-resolved
                        // MathJax.startup object would break MathJax state on
                        // pages where it has already loaded.
                        window.MathJax.startup = window.MathJax.startup || {};
                        window.MathJax.startup.typeset = false;
                        $.ajax({
                            type: 'GET',
                            url: '/static/furaoj/mathjax/4.1.3/tex-chtml.js',
                            dataType: 'script',
                            cache: true,
                            success: update_math
                        });
                    }
                });
            } else {
                update_math();
            }
        }
    })
});

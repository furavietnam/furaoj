jQuery(function ($) {
    function run_typeset(element, $content) {
        if (!element || !window.MathJax) return;
        var doTypeset = function () {
            if (typeof window.MathJax.typesetClear === 'function') {
                try { window.MathJax.typesetClear([element]); } catch (e) {}
            }
            if (typeof window.MathJax.typesetPromise === 'function') {
                window.MathJax.typesetPromise([element]).then(function () {
                    $content.find('.tex-image').hide();
                    $content.find('.tex-text').show();
                }).catch(function (err) {
                    console.warn('MathJax preview typeset warning:', err);
                });
            } else if (window.MathJax.Hub && window.MathJax.Hub.Queue) {
                window.MathJax.Hub.Queue(['Typeset', window.MathJax.Hub, element]);
            }
        };

        if (window.MathJax.startup && window.MathJax.startup.promise
            && typeof window.MathJax.startup.promise.then === 'function') {
            window.MathJax.startup.promise.then(doTypeset);
        } else {
            doTypeset();
        }
    }

    $(document).on('martor:preview', function (e, $content) {
        var el = $content ? $content[0] : null;
        if (!el) return;

        if (window.MathJax && (typeof window.MathJax.typesetPromise === 'function' || (window.MathJax.startup && window.MathJax.startup.promise))) {
            run_typeset(el, $content);
            return;
        }

        var $jax = $content.find('.require-mathjax-support');
        if ($jax.length) {
            if (!('MathJax' in window)) {
                $.ajax({
                    type: 'GET',
                    url: $jax.attr('data-config') || '/static/mathjax_config.js',
                    dataType: 'script',
                    cache: true,
                    success: function () {
                        window.MathJax = window.MathJax || {};
                        window.MathJax.startup = window.MathJax.startup || {};
                        window.MathJax.startup.typeset = false;
                        $.ajax({
                            type: 'GET',
                            url: '/static/furaoj/mathjax/4.1.3/tex-chtml.js',
                            dataType: 'script',
                            cache: true,
                            success: function () {
                                run_typeset(el, $content);
                            }
                        });
                    }
                });
            } else {
                run_typeset(el, $content);
            }
        }
    });
});

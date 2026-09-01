window.MathJax = {
    loader: {
        load: ['[tex]/color'],
        paths: {
            mathjax: '/static/furaoj/mathjax/4.1.3'
        }
    },
    tex: {
        packages: {
            '[+]': ['color']
        },
        inlineMath: [
            ['$', '$'],
            ['\\(', '\\)']
        ]
    },
    options: {
        enableMenu: false
    }
};
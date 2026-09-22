var active_tooltip = null;

function display_tooltip(where) {
    if (active_tooltip !== null) {
        active_tooltip.removeClass(['tooltipped', 'tooltipped-n', 'tooltipped-ne', 'tooltipped-nw', 'tooltipped-e', 'tooltipped-w']).removeAttr('aria-label');
    }
    if (where !== null) {
        var day_num = parseInt(where.attr('data-day'));
        var tooltip_direction = day_num < 15 ? 'tooltipped-ne' : (day_num > 350 ? 'tooltipped-nw' : 'tooltipped-n');
        where.addClass(['tooltipped', tooltip_direction])
            .attr('aria-label', where.attr('data-submission-activity'));
    }
    active_tooltip = where;
}

function install_tooltips($) {
    display_tooltip(null);
    $('.activity-label').each(function () {
        var link = $(this);
        link.hover(
            function () {
                display_tooltip(link);
            },
            function () {
                display_tooltip(null);
            }
        );
    });
}

function init_submission_table($, submission_activity, language_code) {
    var activity_levels = 5; // 5 levels of activity
    var current_year = new Date().getFullYear();
    var keys = Object.keys(submission_activity || {});
    var parsed_min_year = keys.length > 0 ? new Date(Math.min(...keys.map(Date.parse))).getFullYear() : current_year;
    var min_year = isNaN(parsed_min_year) ? current_year - 1 : Math.min(parsed_min_year, current_year - 1);
    var $div = $('#submission-activity');

    function draw_contribution(year) {
        $div.find('#submission-activity-table tbody td').remove();
        $div.find('#submission-months th:not(.submission-date-col)').remove();
        $('#year').attr('data-year', year);
        $('#prev-year-action').css('display', year > min_year ? '' : 'none');
        $('#next-year-action').css('display', year < current_year ? '' : 'none');

        var start_day = new Date(year, 0, 1);
        var end_day = new Date(year + 1, 0, 0);
        if (year == current_year) {
            end_day = new Date();
            start_day = new Date(end_day.getFullYear() - 1, end_day.getMonth(), end_day.getDate() + 1);
            $('#year').text(gettext('past year'));
        } else {
            $('#year').text(year);
        }

        var days = [];
        for (var day = start_day, day_num = 1; day <= end_day; day.setDate(day.getDate() + 1), day_num++) {
            var isodate = day.toISOString().split('T')[0];
            days.push({
                date: new Date(day),
                weekday: day.getDay(),
                day_num: day_num,
                activity: submission_activity[isodate] || 0,
            });
        }

        // Calculate statistics
        var sum_activity = days.reduce(function (sum, obj) { return sum + obj.activity; }, 0);
        var active_days = days.filter(function (obj) { return obj.activity > 0; }).length;

        var max_streak = 0;
        var temp_streak = 0;
        for (var i = 0; i < days.length; i++) {
            if (days[i].activity > 0) {
                temp_streak++;
                if (temp_streak > max_streak) max_streak = temp_streak;
            } else {
                temp_streak = 0;
            }
        }

        var current_streak = 0;
        if (days.length > 0) {
            var lastIdx = days.length - 1;
            var startIdx = -1;
            if (days[lastIdx].activity > 0) {
                startIdx = lastIdx;
            } else if (lastIdx > 0 && days[lastIdx - 1].activity > 0) {
                startIdx = lastIdx - 1;
            }

            if (startIdx >= 0) {
                for (var k = startIdx; k >= 0; k--) {
                    if (days[k].activity > 0) {
                        current_streak++;
                    } else {
                        break;
                    }
                }
            }
        }

        // Update stats boxes
        $('#stat-total-submissions').text(sum_activity);
        $('#stat-active-days').text(active_days);
        $('#stat-max-streak').text(max_streak);
        $('#stat-current-streak').text(current_streak);

        $div.find('#submission-total-count').text(
            ngettext('%(cnt)d total submission', '%(cnt)d total submissions', sum_activity)
                .replace('%(cnt)d', sum_activity)
        );

        if (year == current_year) {
            $('#submission-activity-header').text(
                ngettext('%(cnt)d submission in the last year', '%(cnt)d submissions in the last year', sum_activity)
                    .replace('%(cnt)d', sum_activity)
            );
        } else {
            $('#submission-activity-header').text(
                ngettext('%(cnt)d submission in %(year)d', '%(cnt)d submissions in %(year)d', sum_activity)
                    .replace('%(cnt)d', sum_activity)
                    .replace('%(year)d', year)
            );
        }

        // Build month labels row
        var total_columns = Math.ceil((days[0].weekday + days.length) / 7);
        var month_starts = [];
        var last_month = -1;

        for (var c = 0; c < total_columns; c++) {
            var start_idx = Math.max(0, c * 7 - days[0].weekday);
            var end_idx = Math.min(days.length - 1, (c + 1) * 7 - 1 - days[0].weekday);
            var m = -1;
            var rep_day = null;
            for (var idx = start_idx; idx <= end_idx; idx++) {
                if (days[idx].date.getDate() === 1) {
                    m = days[idx].date.getMonth();
                    rep_day = days[idx].date;
                    break;
                }
            }
            if (m === -1) {
                var mid_idx = Math.floor((start_idx + end_idx) / 2);
                m = days[mid_idx].date.getMonth();
                rep_day = days[mid_idx].date;
            }

            if (m !== last_month) {
                var month_name;
                if (language_code && language_code.indexOf('vi') === 0) {
                    month_name = 'Thg ' + (m + 1);
                } else {
                    month_name = rep_day.toLocaleDateString(language_code, { month: 'short' });
                }
                month_starts.push({
                    col: c,
                    name: month_name
                });
                last_month = m;
            }
        }

        var $monthRow = $div.find('#submission-months');
        for (var m_i = 0; m_i < month_starts.length; m_i++) {
            var next_col = (m_i + 1 < month_starts.length) ? month_starts[m_i + 1].col : total_columns;
            var span = next_col - month_starts[m_i].col;
            var labelText = span >= 2 ? month_starts[m_i].name : '';
            $monthRow.append(
                $('<th>').addClass('month-label').attr('colspan', span).text(labelText)
            );
        }

        // Blank cells before day 0
        for (var current_weekday = 0; current_weekday < days[0].weekday; current_weekday++) {
            $div.find('#submission-' + current_weekday)
                .append($('<td>').addClass('activity-blank').append('<div>'));
        }

        var max_activity = Math.max(1, Math.max.apply(null, days.map(function (obj) { return obj.activity; })));
        days.forEach(function (obj) {
            var level = obj.activity === 0 ? 0 : Math.max(1, Math.min(4, Math.ceil((obj.activity / max_activity) * (activity_levels - 1))));
            var dateStr;
            if (language_code && language_code.indexOf('vi') === 0) {
                dateStr = obj.date.getDate() + ' Thg ' + (obj.date.getMonth() + 1) + ', ' + obj.date.getFullYear();
            } else {
                dateStr = obj.date.toLocaleDateString(
                    language_code,
                    { month: 'short', year: 'numeric', day: 'numeric' }
                );
            }
            var text = ngettext('%(cnt)d submission on %(date)s', '%(cnt)d submissions on %(date)s', obj.activity)
                .replace('%(cnt)d', obj.activity)
                .replace('%(date)s', dateStr);

            $div.find('#submission-' + obj.weekday)
                .append(
                    $('<td>').addClass(['activity-label', 'activity-' + level])
                        .attr('data-submission-activity', text)
                        .attr('data-day', obj.day_num)
                        .append('<div>')
                );
        });

        install_tooltips($);
    }

    $('#prev-year-action').click(function () {
        draw_contribution(parseInt($('#year').attr('data-year')) - 1);
    });
    $('#next-year-action').click(function () {
        draw_contribution(parseInt($('#year').attr('data-year')) + 1);
    });

    draw_contribution(current_year);
    $('#submission-activity').css('display', '');
}

window.init_submission_table = init_submission_table;


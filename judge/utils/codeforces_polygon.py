import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from operator import itemgetter
from typing import BinaryIO, List, Union

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from lxml import etree as ET

from judge.models import Language, Problem, ProblemData, ProblemGroup, ProblemTestCase, ProblemTranslation, \
    ProblemType, Profile, Solution
from judge.utils.problem_data import ProblemDataCompiler
from judge.views.widgets import django_uploader

__all__ = ['ImportPolygonError', 'PolygonImporter']

logger = logging.getLogger('judge.polygon')


PANDOC_FILTER = r"""
local function normalize_quote(text)
    text = text:gsub('\u{2018}', "'")
    text = text:gsub('\u{2019}', "'")
    text = text:gsub('\u{201C}', '"')
    text = text:gsub('\u{201D}', '"')
    return text
end

local function escape_html_content(text)
    text = text:gsub('&', '&amp;')
    text = text:gsub('<', "&lt;")
    text = text:gsub('>', "&gt;")
    text = text:gsub('*', '\\*')
    text = text:gsub('_', '\\_')
    return text
end

function Math(m)
    local delimiter = m.mathtype == 'InlineMath' and '$' or '$$'
    return pandoc.RawInline('html', delimiter .. m.text .. delimiter)
end

function Image(el)
    return {pandoc.RawInline('markdown', '\n\n'), el, pandoc.RawInline('markdown', '\n\n')}
end

function Code(el)
    local text = normalize_quote(el.text)
    text = escape_html_content(text)
    return pandoc.RawInline('html', '<span style="font-family: courier new,monospace;">' .. text .. '</span>')
end

function CodeBlock(el)
    el.text = normalize_quote(el.text)
    if el.classes[1] == nil then
        el.classes[1] = ''
    end
    return el
end

function Quoted(el)
    local quote = el.quotetype == 'SingleQuote' and "'" or '"'
    local inlines = el.content
    table.insert(inlines, 1, quote)
    table.insert(inlines, quote)
    return inlines
end

function Str(el)
    el.text = normalize_quote(el.text)

    local res = {}
    local part = ''
    for c in el.text:gmatch(utf8.charpattern) do
        if c == '\u{2013}' then
            if part ~= '' then
                table.insert(res, pandoc.Str(part))
                part = ''
            end
            table.insert(res, pandoc.RawInline('html', '&ndash;'))
        elseif c == '\u{2014}' then
            if part ~= '' then
                table.insert(res, pandoc.Str(part))
                part = ''
            end
            table.insert(res, pandoc.RawInline('html', '&mdash;'))
        elseif c == '\u{00A0}' then
            if part ~= '' then
                table.insert(res, pandoc.Str(part))
                part = ''
            end
            table.insert(res, pandoc.RawInline('html', '&nbsp;'))
        else
            part = part .. c
        end
    end
    if part ~= '' then
        table.insert(res, pandoc.Str(part))
    end

    return res
end

local FOOTNOTE_SYMBOLS = {'\u{2217}', '\u{2020}', '\u{2021}', '\u{00A7}', '\u{00B6}'}
local footnote_counter = 0
local footnotes = {}

local function footnote_label(n)
    local idx = ((n - 1) % #FOOTNOTE_SYMBOLS) + 1
    local rep = math.floor((n - 1) / #FOOTNOTE_SYMBOLS) + 1
    return string.rep(FOOTNOTE_SYMBOLS[idx], rep)
end

function Note(el)
    footnote_counter = footnote_counter + 1
    local sym = footnote_label(footnote_counter)
    local content = el.content
    local label = pandoc.RawInline('html', '<strong>' .. sym .. '</strong> ')
    if #content > 0 and content[1].t == 'Para' then
        table.insert(content[1].content, 1, label)
    else
        table.insert(content, 1, pandoc.Para({label}))
    end
    table.insert(footnotes, content)
    return pandoc.RawInline('html', '<sup>' .. sym .. '</sup>')
end

function Pandoc(doc)
    if #footnotes == 0 then return doc end
    table.insert(doc.blocks, pandoc.RawBlock('html', '<hr style="width: 25%;">'))
    for _, content in ipairs(footnotes) do
        for _, block in ipairs(content) do
            table.insert(doc.blocks, block)
        end
    end
    return doc
end

function Div(el)
    if el.classes[1] == 'center' then
        local res = {}
        table.insert(res, pandoc.RawBlock('markdown', '<' .. el.classes[1] .. '>'))
        for _, block in ipairs(el.content) do
            table.insert(res, block)
        end
        table.insert(res, pandoc.RawBlock('markdown', '</' .. el.classes[1] .. '>'))
        return res

    elseif el.classes[1] == 'epigraph' then
        local filter = {
            Math = Math,
            Code = Code,
            Quoted = Quoted,
            Str = Str,
            Para = function (s)
                return pandoc.Plain(s.content)
            end,
            Span = function (s)
                return s.content
            end
        }

        function renderHTML(el)
            local doc = pandoc.Pandoc({el})
            local rendered = pandoc.write(doc:walk(filter), 'html')
            return pandoc.RawBlock('markdown', rendered)
        end

        local res = {}
        table.insert(res, pandoc.RawBlock('markdown', '<div style="margin-left: 67%;">'))
        if el.content[1] then
            table.insert(res, renderHTML(el.content[1]))
        end
        table.insert(res, pandoc.RawBlock('markdown', '<div style="border-top: 1px solid #888;"></div>'))
        if el.content[2] then
            table.insert(res, renderHTML(el.content[2]))
        end
        table.insert(res, pandoc.RawBlock('markdown', '</div>'))
        return res
    end

    return nil
end
"""


TEX_MACROS = r"""
\renewcommand{\bf}{\textbf}
\renewcommand{\it}{\textit}
\renewcommand{\tt}{\texttt}
\renewcommand{\t}{\texttt}
\providecommand{\text}{\mathrm}
\providecommand{\textbf}[1]{\mathbf{#1}}
\providecommand{\textit}[1]{\mathit{#1}}
"""


def pandoc_tex_to_markdown(tex):
    if not tex or not isinstance(tex, str) or not tex.strip():
        return ''

    tex_full = TEX_MACROS + '\n' + tex
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(os.path.join(tmp_dir, 'temp.tex'), 'w', encoding='utf-8') as f:
            f.write(tex_full)

        with open(os.path.join(tmp_dir, 'filter.lua'), 'w', encoding='utf-8') as f:
            f.write(PANDOC_FILTER)

        try:
            subprocess.run(
                ['pandoc', '--lua-filter=filter.lua', '-t', 'gfm', '-o', 'temp.md', 'temp.tex'],
                cwd=tmp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning('Pandoc lua filter failed with code %d: %s. Trying direct markdown conversion.', e.returncode, e.stderr)
            try:
                subprocess.run(
                    ['pandoc', '-t', 'gfm', '-o', 'temp.md', 'temp.tex'],
                    cwd=tmp_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                # Fallback an toan: tra ve text goc, tuyet doi khong gay crash
                return tex

        with open(os.path.join(tmp_dir, 'temp.md'), 'r', encoding='utf-8') as f:
            md = f.read()

    return md


def pandoc_get_version():
    parts = subprocess.check_output(['pandoc', '--version']).decode().splitlines()[0].split(' ')[1].split('.')
    return tuple(map(int, parts))


class ImportPolygonError(Exception):
    pass


class PolygonImporter:
    def __init__(
            self,
            package: Union[str, BinaryIO],
            code: str,
            authors: List[Profile] = None,
            curators: List[Profile] = None,
            do_update: bool = False,
            interactive: bool = True,
            config=None,
    ):
        if not shutil.which('pandoc'):
            raise ImportPolygonError('pandoc not installed')
        if pandoc_get_version() < (3, 0, 0):
            raise ImportPolygonError('pandoc version must be at least 3.0.0')

        if not interactive and config is None:
            raise ImportPolygonError('config must be provided if interactive is False')
        self.interactive = interactive
        self.config = config

        Problem._meta.get_field('code').run_validators(code)
        if Problem.objects.filter(code=code).exists():
            if not do_update:
                raise ImportPolygonError(f'problem with code {code} already exists')
        elif do_update:
            raise ImportPolygonError(f'problem with code {code} not found')

        self.package = zipfile.ZipFile(package, 'r')
        if 'problem.xml' not in self.package.namelist():
            raise ImportPolygonError('problem.xml not found')

        self.root = ET.fromstring(self.package.read('problem.xml'))

        self.meta = {}
        self.meta['code'] = code
        self.meta['authors'] = authors or []
        self.meta['curators'] = curators or []

        self.validate()

    def validate(self):
        testset = self.root.find('.//testset[@name="tests"]')
        if testset is None:
            raise ImportPolygonError('testset tests not found')

        if len(testset.find('tests').getchildren()) == 0:
            raise ImportPolygonError('no testcases found')

        input_path_pattern = testset.find('input-path-pattern').text
        input_path = input_path_pattern % 1
        if input_path not in self.package.namelist():
            raise ImportPolygonError('not full package')

    def run(self):
        try:
            self.meta['image_cache'] = {}
            self.meta['tmp_dir'] = tempfile.TemporaryDirectory()
            self.parse_assets()
            self.parse_tests()
            self.parse_statements()
            self.parse_solutions()
            self.update_or_create_problem()
        except Exception:
            for image_url in self.meta['image_cache'].values():
                path = default_storage.path(os.path.join(settings.MARTOR_UPLOAD_MEDIA_DIR, os.path.basename(image_url)))
                if os.path.exists(path):
                    os.remove(path)
            raise
        finally:
            if 'tmp_dir' in self.meta:
                self.meta['tmp_dir'].cleanup()

    def log(self, *args, **kwargs):
        if self.interactive:
            print(*args, **kwargs)

    def parse_assets(self):
        interactor = self.root.find('.//interactor')
        if interactor is None:
            self.log('Use standard grader.')
            self.meta['grader'] = 'standard'
        else:
            self.log('Found interactor. Use interactive grader.')
            self.meta['grader'] = 'interactive'
            self.meta['custom_grader'] = os.path.join(self.meta['tmp_dir'].name, 'interactor.cpp')

            source = interactor.find('source')
            if source is None:
                raise ImportPolygonError('interactor source not found. how possible?')

            path = source.get('path')
            if not path.lower().endswith('.cpp'):
                raise ImportPolygonError('interactor must use C++')

            with open(self.meta['custom_grader'], 'wb') as f:
                f.write(self.package.read(path))

            self.log('NOTE: checker is ignored when using interactive grader.')
            self.log('If you use custom checker, please merge it with the interactor.')
            self.meta['checker'] = 'standard'
            return

        checker = self.root.find('.//checker')
        if checker is None:
            raise ImportPolygonError('checker not found')

        if checker.get('type') != 'testlib':
            raise ImportPolygonError('not a testlib checker. how possible?')

        checker_name = checker.get('name')
        if checker_name is None:
            self.meta['checker'] = 'bridged'
        else:
            if checker_name in ['std::hcmp.cpp', 'std::ncmp.cpp', 'std::wcmp.cpp']:
                self.meta['checker'] = 'standard'
                self.log('Use standard checker.')
            elif checker_name in ['std::rcmp4.cpp', 'std::rcmp6.cpp', 'std::rcmp9.cpp']:
                self.meta['checker'] = 'floats'
                self.meta['checker_args'] = {'precision': int(checker_name[9])}
                self.log(f'Use floats checker with precision {self.meta["checker_args"]["precision"]}.')
            elif checker_name == 'std::fcmp.cpp':
                self.meta['checker'] = 'identical'
                self.log('Use identical checker.')
            elif checker_name == 'std::lcmp.cpp':
                self.meta['checker'] = 'linecount'
                self.log('Use linecount checker.')
            else:
                self.meta['checker'] = 'bridged'

        if self.meta['checker'] == 'bridged':
            self.log('Use custom checker.')

            source = checker.find('source')
            if source is None:
                raise ImportPolygonError('checker source not found. how possible?')

            path = source.get('path')
            if not path.lower().endswith('.cpp'):
                raise ImportPolygonError('checker must use C++')

            self.meta['checker_args'] = {
                'files': 'checker.cpp',
                'lang': 'CPP20',
                'type': 'testlib',
            }

            judging = self.root.find('.//judging')
            if judging is not None and judging.get('treat-points-from-checker-as-percent', '') == 'true':
                self.meta['checker_args']['treat_checker_points_as_percentage'] = True

            self.meta['custom_checker'] = os.path.join(self.meta['tmp_dir'].name, 'checker.cpp')
            with open(self.meta['custom_checker'], 'wb') as f:
                f.write(self.package.read(path))

    def parse_tests(self):
        testset = self.root.find('.//testset[@name="tests"]')
        if testset is None:
            raise ImportPolygonError('testset tests not found')

        if len(testset.find('tests').getchildren()) == 0:
            raise ImportPolygonError('no testcases found')

        self.meta['time_limit'] = float(testset.find('time-limit').text) / 1000
        self.meta['memory_limit'] = int(testset.find('memory-limit').text) // 1024

        if hasattr(settings, 'DMOJ_PROBLEM_MIN_MEMORY_LIMIT'):
            self.meta['memory_limit'] = max(self.meta['memory_limit'], settings.DMOJ_PROBLEM_MIN_MEMORY_LIMIT)
        if hasattr(settings, 'DMOJ_PROBLEM_MAX_MEMORY_LIMIT'):
            self.meta['memory_limit'] = min(self.meta['memory_limit'], settings.DMOJ_PROBLEM_MAX_MEMORY_LIMIT)

        self.log(f'Time limit: {self.meta["time_limit"]}s')
        self.log(f'Memory limit: {self.meta["memory_limit"] // 1024}MB')

        self.meta['cases_data'] = []
        self.meta['batches'] = {}
        self.meta['normal_cases'] = []
        self.meta['zipfile'] = os.path.join(self.meta['tmp_dir'].name, 'tests.zip')

        groups = testset.find('groups')
        if groups is not None:
            for group in groups.getchildren():
                name = group.get('name')
                points = float(group.get('points', 0))
                points_policy = group.get('points-policy')
                dependencies = group.find('dependencies')
                if dependencies is None:
                    dependencies = []
                else:
                    dependencies = [d.get('group') for d in dependencies.getchildren()]

                assert points_policy in ['complete-group', 'each-test']
                if points_policy == 'each-test' and len(dependencies) > 0:
                    raise ImportPolygonError(
                        'dependencies are only supported for batches with complete-group point policy',
                    )

                self.meta['batches'][name] = {
                    'name': name,
                    'points': points,
                    'points_policy': points_policy,
                    'dependencies': dependencies,
                    'cases': [],
                }

        with zipfile.ZipFile(self.meta['zipfile'], 'w') as tests_zip:
            input_path_pattern = testset.find('input-path-pattern').text
            answer_path_pattern = testset.find('answer-path-pattern').text
            for i, test in enumerate(testset.find('tests').getchildren()):
                points = float(test.get('points', 0))
                input_path = input_path_pattern % (i + 1)
                answer_path = answer_path_pattern % (i + 1)
                input_file = f'{(i + 1):02d}.inp'
                output_file = f'{(i + 1):02d}.out'

                tests_zip.writestr(input_file, self.package.read(input_path))
                tests_zip.writestr(output_file, self.package.read(answer_path))

                self.meta['cases_data'].append({
                    'index': i,
                    'input_file': input_file,
                    'output_file': output_file,
                    'points': points,
                })

                group = test.get('group', '')
                if group in self.meta['batches']:
                    self.meta['batches'][group]['cases'].append(i)
                else:
                    self.meta['normal_cases'].append(i)

        def get_tests_by_batch(name):
            batch = self.meta['batches'][name]

            if len(batch['dependencies']) == 0:
                return batch['cases']

            cases = set(batch['cases'])
            for dependency in batch['dependencies']:
                cases.update(get_tests_by_batch(dependency))

            batch['dependencies'] = []
            batch['cases'] = list(cases)
            return batch['cases']

        each_test_batches = []
        for batch in self.meta['batches'].values():
            if batch['points_policy'] == 'each-test':
                each_test_batches.append(batch['name'])
                self.meta['normal_cases'] += batch['cases']
                continue

            batch['cases'] = get_tests_by_batch(batch['name'])

        for batch in each_test_batches:
            del self.meta['batches'][batch]

        all_points = [batch['points'] for batch in self.meta['batches'].values()] + \
                     [self.meta['cases_data'][i]['points'] for i in self.meta['normal_cases']]
        if any(not p.is_integer() for p in all_points):
            self.log('Found fractional points. Normalize to integers.')
            all_points = [int(p * 1000) for p in all_points]
            gcd = math.gcd(*all_points)
            for batch in self.meta['batches'].values():
                batch['points'] = int(batch['points'] * 1000) // gcd
            for i in self.meta['normal_cases']:
                case_data = self.meta['cases_data'][i]
                case_data['points'] = int(case_data['points'] * 1000) // gcd

        total_points = (sum(b['points'] for b in self.meta['batches'].values()) +
                        sum(self.meta['cases_data'][i]['points'] for i in self.meta['normal_cases']))
        if total_points == 0:
            self.log('Total points is zero. Set partial to False.')
            self.meta['partial'] = False
        else:
            self.log('Total points is non-zero. Set partial to True.')
            self.meta['partial'] = True

        if self.meta['partial']:
            zero_point_batches = [name for name, batch in self.meta['batches'].items() if batch['points'] == 0]
            if len(zero_point_batches) > 0:
                if self.interactive:
                    self.log('Found zero-point batches:', ', '.join(zero_point_batches))
                    self.log('Would you like to ignore them (y/n)? ', end='', flush=True)
                    ignore_zero_point_batches = input().lower() in ['y', 'yes']
                else:
                    ignore_zero_point_batches = self.config.get('ignore_zero_point_batches', False)

                if ignore_zero_point_batches:
                    self.meta['batches'] = {
                        name: batch for name, batch in self.meta['batches'].items() if batch['points'] > 0
                    }
                    self.log(f'Ignored {len(zero_point_batches)} zero-point batches.')

            zero_point_cases_count = len([
                idx for idx in self.meta['normal_cases'] if self.meta['cases_data'][idx]['points'] == 0
            ])
            if zero_point_cases_count > 0:
                if self.interactive:
                    self.log(f'Found {zero_point_cases_count} zero-point tests.')
                    self.log('Would you like to ignore them (y/n)? ', end='', flush=True)
                    ignore_zero_point_cases = input().lower() in ['y', 'yes']
                else:
                    ignore_zero_point_cases = self.config.get('ignore_zero_point_cases', False)

                if ignore_zero_point_cases:
                    self.meta['normal_cases'] = [
                        idx for idx in self.meta['normal_cases'] if self.meta['cases_data'][idx]['points'] > 0
                    ]
                    self.log(f'Ignored {zero_point_cases_count} zero-point tests.')

        self.meta['normal_cases'].sort()
        for batch in self.meta['batches'].values():
            batch['cases'].sort()

        self.log(f'Parsed {len(self.meta["batches"])} batches and {len(self.meta["normal_cases"])} normal tests!')

        self.meta['grader_args'] = {}
        judging = self.root.find('.//judging')
        if judging is not None:
            io_input_file = judging.get('input-file', '')
            io_output_file = judging.get('output-file', '')

            if io_input_file != '' and io_output_file != '':
                self.log('Use File IO.')
                self.log('Input file:', io_input_file)
                self.log('Output file:', io_output_file)
                self.meta['grader_args']['io_method'] = 'file'
                self.meta['grader_args']['io_input_file'] = io_input_file
                self.meta['grader_args']['io_output_file'] = io_output_file

    def parse_statements(self):
        self.meta['name'] = ''
        self.meta['description'] = ''
        self.meta['translations'] = []
        self.meta['tutorial'] = ''

        def process_images(text):
            if not text:
                return ''
            image_cache = self.meta['image_cache']

            def save_image(image_path):
                norm_path = os.path.normpath(os.path.join(statement_folder, image_path))
                sha1 = hashlib.sha1()
                sha1.update(self.package.open(norm_path, 'r').read())
                sha1 = sha1.hexdigest()

                if sha1 not in image_cache:
                    image = File(
                        file=self.package.open(norm_path, 'r'),
                        name=os.path.basename(image_path),
                    )
                    data = json.loads(django_uploader(image))
                    image_cache[sha1] = data['link']

                return image_cache[sha1]

            for image_path in set(re.findall(r'!\[image\]\((.+?)\)', text)):
                try:
                    text = text.replace(
                        f'![image]({image_path})',
                        f'![image]({save_image(image_path)})',
                    )
                except Exception as e:
                    self.log(f'Warning: Failed to extract image {image_path}: {e}')

            for img_tag in set(re.findall(r'<\s*img[^>]*>', text)):
                try:
                    image_path = re.search(r'<\s*img[^>]+src\s*=\s*(["\'])(.*?)\1[^>]*>', img_tag).group(2)
                    text = text.replace(
                        img_tag,
                        img_tag.replace(image_path, save_image(image_path)),
                    )
                except Exception as e:
                    self.log(f'Warning: Failed to extract img tag {img_tag}: {e}')

            return text

        def parse_problem_properties(problem_properties):
            description = ''

            # Legend
            legend = problem_properties.get('legend')
            if legend:
                description += pandoc_tex_to_markdown(legend)

            # Input
            input_text = problem_properties.get('input')
            if input_text:
                description += '\n## Input\n\n'
                description += pandoc_tex_to_markdown(input_text)

            # Output
            output_text = problem_properties.get('output')
            if output_text:
                description += '\n## Output\n\n'
                description += pandoc_tex_to_markdown(output_text)

            # Interaction
            if problem_properties.get('interaction'):
                description += '\n## Interaction\n\n'
                description += pandoc_tex_to_markdown(problem_properties['interaction'])

            # Scoring
            if problem_properties.get('scoring'):
                description += '\n## Scoring\n\n'
                description += pandoc_tex_to_markdown(problem_properties['scoring'])

            # Sample tests
            for i, sample in enumerate(problem_properties.get('sampleTests', []), start=1):
                description += f'\n## Sample Input {i}\n\n'
                description += '```\n' + sample.get('input', '').strip() + '\n```\n'
                description += f'\n## Sample Output {i}\n\n'
                description += '```\n' + sample.get('output', '').strip() + '\n```\n'

            # Notes
            notes = problem_properties.get('notes')
            if notes:
                description += '\n## Notes\n\n'
                description += pandoc_tex_to_markdown(notes)

            return description

        def input_choice(prompt, choices):
            while True:
                choice = input(prompt)
                if choice in choices:
                    return choice
                else:
                    self.log('Invalid choice')

        statements = self.root.findall('.//statement[@type="application/x-tex"]')
        if len(statements) == 0:
            if not self.interactive:
                return

            self.log('Statement not found! Would you like to skip statement (y/n)? ', end='', flush=True)
            if input().lower() in ['y', 'yes']:
                return

            raise ImportPolygonError('statement not found')

        translations = []
        tutorials = []
        for statement in statements:
            language = statement.get('language', 'unknown')
            statement_folder = os.path.dirname(statement.get('path'))
            problem_properties_path = os.path.join(statement_folder, 'problem-properties.json')
            if problem_properties_path not in self.package.namelist():
                raise ImportPolygonError(f'problem-properties.json not found at path {problem_properties_path}')

            problem_properties = json.loads(self.package.read(problem_properties_path).decode('utf-8'))

            description = parse_problem_properties(problem_properties)
            translations.append({
                'language': language,
                'description': process_images(description),
            })

            tutorial = problem_properties.get('tutorial')
            if isinstance(tutorial, str) and tutorial.strip() != '':
                try:
                    tutorial = pandoc_tex_to_markdown(tutorial)
                except Exception as e:
                    self.log(f'Warning: failed to convert tutorial ({e}), using raw text')
                tutorials.append({
                    'language': language,
                    'tutorial': tutorial,
                })

        if len(translations) > 1:
            languages = [t['language'] for t in translations]
            if self.interactive:
                self.log('Multilingual statements found:', languages)
                main_statement_language = input_choice('Please select one as the main statement: ', languages)
            else:
                main_statement_language = self.config.get('main_statement_language', None)
                if main_statement_language not in languages:
                    raise ImportPolygonError(f'invalid main statement language {main_statement_language}')
        else:
            main_statement_language = translations[0]['language']

        if len(tutorials) > 1:
            languages = [t['language'] for t in tutorials]
            if self.interactive:
                self.log('Multilingual tutorials found:', languages)
                main_tutorial_language = input_choice('Please select one as the sole tutorial: ', languages)
            else:
                main_tutorial_language = self.config.get('main_tutorial_language', None)
                if main_tutorial_language not in languages:
                    raise ImportPolygonError(f'invalid main tutorial language {main_tutorial_language}')

            self.meta['tutorial'] = next(t for t in tutorials if t['language'] == main_tutorial_language)['tutorial']
        elif len(tutorials) > 0:
            self.meta['tutorial'] = tutorials[0]['tutorial']

        self.meta['tutorial'] = process_images(self.meta['tutorial'])

        for t in translations:
            language = t['language']
            description = t['description']
            name_element = self.root.find(f'.//name[@language="{language}"]')
            name = name_element.get('value') if name_element is not None else ''

            if language == main_statement_language:
                self.meta['name'] = name
                self.meta['description'] = description
            else:
                choices = list(map(itemgetter(0), settings.LANGUAGES))
                if self.interactive:
                    site_language = input_choice(
                        f'Please select corresponding site language for {language} '
                        f'(available options are {", ".join(choices)}): ',
                        choices,
                    )
                else:
                    site_language = self.config.get('polygon_to_site_language_map', {}).get(language, None)
                    if site_language not in choices:
                        raise ImportPolygonError(f'invalid site language for {language}')

                self.meta['translations'].append({
                    'language': site_language,
                    'name': name,
                    'description': description,
                })

    def parse_solutions(self):
        solutions = self.root.find('.//solutions')
        if solutions is None:
            return

        main_solution = solutions.find('solution[@tag="main"]')
        if main_solution is None:
            return

        if not self.interactive:
            if not self.config.get('append_main_solution_to_tutorial', False):
                return
        else:
            self.log('Main solution found. Would you like to append it to the tutorial (y/n)? ', end='', flush=True)
            if input().lower() not in ['y', 'yes']:
                return

        source = main_solution.find('source')
        if source is None:
            return

        source_code = self.package.read(source.get('path')).decode('utf-8').strip()
        source_lang = source.get('type')
        markdown_lang = ''
        if source_lang.startswith('cpp'):
            markdown_lang = 'cpp'
        elif source_lang.startswith('python'):
            markdown_lang = 'python'
        elif source_lang.startswith('java'):
            markdown_lang = 'java'

        self.meta['tutorial'] = self.meta['tutorial'].rstrip() + f"""\n
<blockquote class="spoiler">
```{markdown_lang}
{source_code}

```

```
@transaction.atomic
def update_or_create_problem(self):
    self.log('Creating/Updating problem in database.')
    problem, _ = Problem.objects.update_or_create(code=self.meta['code'], defaults={
        'code': self.meta['code'],
        'name': self.meta['name'],
        'time_limit': self.meta['time_limit'],
        'memory_limit': self.meta['memory_limit'],
        'description': self.meta['description'],
        'partial': self.meta['partial'],
        'group': ProblemGroup.objects.order_by('id').first(),
        'points': 0.01,
    })
    problem.save()
    problem.allowed_languages.set(Language.objects.filter(include_in_problem=True))
    problem.authors.set(self.meta['authors'])
    problem.curators.set(self.meta['curators'])
    problem.types.set([ProblemType.objects.order_by('id').first()])
    problem.save()

    ProblemTranslation.objects.filter(problem=problem).delete()
    for tran in self.meta['translations']:
        ProblemTranslation(
            problem=problem,
            language=tran['language'],
            name=tran['name'],
            description=tran['description'],
        ).save()

    Solution.objects.filter(problem=problem).delete()
    if self.meta['tutorial'].strip() != '':
        Solution(
            problem=problem,
            is_public=False,
            publish_on=timezone.now(),
            content=self.meta['tutorial'].strip(),
        ).save()

    with open(self.meta['zipfile'], 'rb') as f:
        problem_data, _ = ProblemData.objects.update_or_create(problem=problem, defaults={
            'problem': problem,
            'zipfile': File(f),
            'grader': self.meta['grader'],
            'checker': self.meta['checker'],
            'grader_args': json.dumps(self.meta['grader_args']),
        })
        problem_data.save()

    if self.meta['checker'] == 'bridged':
        with open(self.meta['custom_checker'], 'rb') as f:
            problem_data.custom_checker = File(f)
            problem_data.save()

    if 'checker_args' in self.meta:
        problem_data.checker_args = json.dumps(self.meta['checker_args'])
        problem_data.save()

    if 'custom_grader' in self.meta:
        with open(self.meta['custom_grader'], 'rb') as f:
            problem_data.custom_grader = File(f)
            problem_data.save()

    ProblemTestCase.objects.filter(dataset=problem).delete()

    order = 0
    last_case = None

    for batch in self.meta['batches'].values():
        if len(batch['cases']) == 0:
            continue

        order += 1
        start_batch = ProblemTestCase(
            dataset=problem,
            order=order,
            type='S',
            points=batch['points'],
            is_pretest=False,
        )
        start_batch.save()
        last_case = start_batch

        for case_index in batch['cases']:
            order += 1
            case_data = self.meta['cases_data'][case_index]
            case = ProblemTestCase(
                dataset=problem,
                order=order,
                type='C',
                input_file=case_data['input_file'],
                output_file=case_data['output_file'],
                is_pretest=False,
            )
            case.save()

        order += 1
        end_batch = ProblemTestCase(dataset=problem, order=order, type='E', is_pretest=False)
        end_batch.save()

    for case_index in self.meta['normal_cases']:
        order += 1
        case_data = self.meta['cases_data'][case_index]
        last_case = case = ProblemTestCase(
            dataset=problem,
            order=order,
            type='C',
            input_file=case_data['input_file'],
            output_file=case_data['output_file'],
            points=case_data['points'],
            is_pretest=False,
        )
        case.save()

    if not self.meta['partial'] and last_case is not None:
        last_case.points = 1
        last_case.save()

    self.log('Generating init.yml')
    ProblemDataCompiler.generate(
        problem=problem,
        data=problem_data,
        cases=problem.cases.order_by('order'),
        files=zipfile.ZipFile(problem_data.zipfile.path).namelist(),
    )
    assert problem_data.feedback == '', problem_data.feedback

```

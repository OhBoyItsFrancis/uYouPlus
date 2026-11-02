#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Compiles uBlock Origin filter lists into the subset Chromium can enforce.

The sideload adblock patch (0039) indexes the output on the device with
Chromium's own EasyList parser (components/subresource_filter/tools/
rule_parser) and enforces it with the subresource filter. That parser speaks
Adblock Plus syntax only, so this script:

  * evaluates uBO's `!#if` / `!#else` / `!#endif` preprocessor blocks for a
    Chromium-on-iOS environment, and follows `!#include`;
  * applies `$badfilter`;
  * rewrites uBO option aliases to their ABP names (3p -> third-party,
    xhr -> xmlhttprequest, frame -> subdocument, from= -> domain=, ...);
  * drops every rule it cannot enforce faithfully: scriptlets (`##+js`),
    HTML filters (`##^`), procedural cosmetic filters (`:has-text()`, ...),
    `$redirect`, `$removeparam`, `$csp`, `$denyallow`, regex filters, entity
    domains (`google.*`), and so on. Dropping a rule only means that rule is
    not applied; it never turns a rule into a broader one.

Writes three files into --out-dir, which the patch bundles into the app:

  adblock_filters.txt       network rules plus site-specific element hiding
                            rules and their exceptions (indexed on device);
  adblock_generic.css       generic element hiding (`##.ad`) as a stylesheet,
                            minus selectors that some site excepts;
  adblock_generic_exceptions.txt
                            `domain,domain<TAB>selector` for generic selectors
                            excepted on some sites (`site.com#@#.ad`).

usage: compile_filters.py --out-dir DIR LIST [LIST ...]
"""

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request

# The environment uBO would report for Chromium (Blink) on an iPhone.
ENV = {
    'env_chromium': True,
    'env_mobile': True,
    'ext_ublock': True,
    'cap_user_stylesheet': True,
    'false': False,
}  # Anything else (env_firefox, env_safari, ext_ubol, cap_html_filtering,
#    cap_ipaddress, env_mv3, adguard, ...) is false.

# uBO option -> ABP option understood by Chromium's rule parser.
OPTION_ALIASES = {
    '1p': '~third-party',
    'first-party': '~third-party',
    '3p': 'third-party',
    'xhr': 'xmlhttprequest',
    'frame': 'subdocument',
    'css': 'stylesheet',
    'beacon': 'ping',
    'doc': 'document',
    'ehide': 'elemhide',
    'elemhide': 'elemhide',
    'ghide': 'generichide',
}

ELEMENT_TYPES = {
    'other', 'script', 'image', 'stylesheet', 'object', 'xmlhttprequest',
    'subdocument', 'ping', 'media', 'font', 'popup', 'websocket',
}
ACTIVATION_TYPES = {'document', 'elemhide', 'generichide', 'genericblock'}
# Options that can be removed without making the rule match more than before.
DROPPABLE_OPTIONS = {'important', 'all', '_', 'cname'}

PROCEDURAL_COSMETIC = re.compile(
    r':(?:-abp-[a-z-]+|has-text|upward|xpath|matches-css(?:-before|-after)?|'
    r'min-text-length|remove|remove-attr|remove-class|style|others|'
    r'watch-attr|matches-path|matches-attr|matches-prop|matches-media|'
    r'if|if-not|spath|nth-ancestor|contains)\(')
UNSUPPORTED_COSMETIC_SEPARATORS = ('#?#', '#@?#', '#$#', '#@$#', '#%#', '#@%#',
                                   '##+js', '#@#+js', '##^', '#@#^', '$$',
                                   '$@$')
DOMAIN_RE = re.compile(r'^[a-z0-9.-]+$')


def is_safe_selector(selector):
  """Rejects selectors that could change the structure of the stylesheet.

  Each selector becomes its own `selector{display:none!important}` rule, so an
  invalid selector only drops that rule. A stray brace, comment, at-rule or
  unbalanced bracket/quote could swallow the rules after it, so reject those.
  """
  if any(token in selector for token in ('{', '}', '/*', '*/', ';', '@', '<')):
    return False
  if selector.endswith('\\'):
    return False
  stack = []
  quote = None
  escaped = False
  for char in selector:
    if escaped:
      escaped = False
    elif char == '\\':
      escaped = True
    elif quote:
      if char == quote:
        quote = None
    elif char in '"\'':
      quote = char
    elif char in '([':
      stack.append(')' if char == '(' else ']')
    elif char in ')]':
      if not stack or stack.pop() != char:
        return False
  return not stack and not quote and not escaped


class Stats:
  def __init__(self):
    self.url = 0
    self.style_generic = 0
    self.style_specific = 0
    self.dropped = {}

  def drop(self, reason):
    self.dropped[reason] = self.dropped.get(reason, 0) + 1


# ---------------------------------------------------------------------------
# Preprocessor (!#if / !#else / !#endif / !#include)


def eval_condition(expr):
  """Evaluates a uBO `!#if` expression: identifiers, !, &&, ||, ()."""
  tokens = re.findall(r'\(|\)|!|&&|\|\||[A-Za-z_][A-Za-z0-9_]*', expr)
  if ''.join(tokens) != re.sub(r'\s+', '', expr):
    return False  # Unknown syntax: treat the block as not applicable.
  pos = 0

  def peek():
    return tokens[pos] if pos < len(tokens) else None

  def take():
    nonlocal pos
    pos += 1
    return tokens[pos - 1]

  def parse_or():
    value = parse_and()
    while peek() == '||':
      take()
      value = parse_and() or value
    return value

  def parse_and():
    value = parse_not()
    while peek() == '&&':
      take()
      value = parse_not() and value
    return value

  def parse_not():
    if peek() == '!':
      take()
      return not parse_not()
    if peek() == '(':
      take()
      value = parse_or()
      if peek() == ')':
        take()
      return value
    token = take() if peek() else ''
    return ENV.get(token, False)

  try:
    return bool(parse_or())
  except IndexError:
    return False


def fetch(location):
  if re.match(r'^https?://', location):
    with urllib.request.urlopen(location, timeout=60) as response:
      return response.read().decode('utf-8', 'replace')
  with open(location, encoding='utf-8', errors='replace') as f:
    return f.read()


def preprocess(location, seen=None):
  """Yields the lines of a list with directives resolved."""
  seen = seen if seen is not None else set()
  if location in seen:
    return
  seen.add(location)
  stack = []  # One bool per open !#if: whether its active branch is taken.
  for raw in fetch(location).splitlines():
    line = raw.strip()
    if line.startswith('!#if '):
      stack.append(eval_condition(line[5:]))
      continue
    if line == '!#else':
      if stack:
        stack[-1] = not stack[-1]
      continue
    if line == '!#endif':
      if stack:
        stack.pop()
      continue
    if not all(stack):
      continue
    if line.startswith('!#include '):
      target = line[len('!#include '):].strip()
      # uBO only honours includes relative to the including list.
      yield from preprocess(urllib.parse.urljoin(location, target), seen)
      continue
    yield line


# ---------------------------------------------------------------------------
# Network filters


def split_options(rule):
  """Splits `pattern$opt1,opt2` (pattern may be a /regex/ containing '$')."""
  if rule.startswith('/') and rule.rfind('/') > 0:
    end = rule.rfind('/')
    dollar = rule.find('$', end)
  else:
    dollar = rule.rfind('$')
  if dollar < 0:
    return rule, []
  return rule[:dollar], [o for o in rule[dollar + 1:].split(',') if o]


def convert_network(rule, stats):
  is_allow = rule.startswith('@@')
  body = rule[2:] if is_allow else rule
  pattern, options = split_options(body)

  if not pattern:
    stats.drop('empty pattern')
    return None
  if pattern.startswith('/') and pattern.endswith('/') and len(pattern) > 1:
    stats.drop('regex pattern')
    return None
  if DOMAIN_RE.match(pattern) and '.' in pattern and not options:
    # uBO treats a bare hostname line like `||host^`.
    pattern = '||%s^' % pattern

  out = []
  for option in options:
    negated = option.startswith('~')
    name = option[1:] if negated else option
    value = None
    if '=' in name:
      name, value = name.split('=', 1)
    if name in OPTION_ALIASES:
      alias = OPTION_ALIASES[name]
      if alias.startswith('~'):
        negated = not negated
        alias = alias[1:]
      name = alias
    if name == 'from':
      name = 'domain'

    if name == 'domain':
      domains = value.split('|') if value else []
      if not domains or any('*' in d or '/' in d for d in domains):
        stats.drop('domain= with entity or regex')
        return None
      out.append('domain=' + '|'.join(domains))
    elif name == 'document' and not is_allow:
      # Blocking a whole page (uBO's strict blocking) is not supported; still
      # block the host's subresources, which is what the rule protects from.
      continue
    elif name in ELEMENT_TYPES or name in ACTIVATION_TYPES:
      if name in ACTIVATION_TYPES and not is_allow:
        stats.drop('activation option on blocking rule')
        return None
      out.append(('~' if negated else '') + name)
    elif name == 'third-party':
      out.append(('~' if negated else '') + 'third-party')
    elif name == 'match-case' and not negated:
      out.append('match-case')
    elif name in DROPPABLE_OPTIONS and not negated:
      continue
    else:
      stats.drop('unsupported option $' + name)
      return None

  stats.url += 1
  result = ('@@' if is_allow else '') + pattern
  return result + ('$' + ','.join(out) if out else '')


# ---------------------------------------------------------------------------
# Cosmetic filters


def convert_cosmetic(rule, separator_pos, stats):
  separator = '#@#' if rule.startswith('#@#', separator_pos) else '##'
  domains_part = rule[:separator_pos]
  selector = rule[separator_pos + len(separator):].strip()

  if not selector or PROCEDURAL_COSMETIC.search(selector):
    stats.drop('procedural cosmetic filter')
    return None
  if not is_safe_selector(selector):
    stats.drop('cosmetic filter with unsafe CSS')
    return None

  domains = []
  had_domains = bool(domains_part.strip())
  for domain in (d.strip() for d in domains_part.split(',')):
    if not domain:
      continue
    if domain.startswith('~'):
      # Chromium's parser has no exclusions for style rules. Dropping the
      # exclusion would apply the rule on sites it was meant to spare.
      stats.drop('cosmetic filter with ~domain')
      return None
    if '*' in domain or '/' in domain or not DOMAIN_RE.match(domain):
      continue  # Entity (google.*) or regex domain: skip just this entry.
    domains.append(domain)
  if had_domains and not domains:
    # Never turn a site-specific rule into a generic one.
    stats.drop('cosmetic filter with only entity/regex domains')
    return None

  if domains:
    stats.style_specific += 1
  else:
    stats.style_generic += 1
  return ','.join(domains) + separator + selector


def convert(line, stats):
  if not line or line.startswith('!') or line.startswith('['):
    return None
  for separator in UNSUPPORTED_COSMETIC_SEPARATORS:
    if separator in line:
      stats.drop('scriptlet / html / procedural syntax')
      return None
  for separator in ('#@#', '##'):
    pos = line.find(separator)
    if pos >= 0:
      return convert_cosmetic(line, pos, stats)
  return convert_network(line, stats)


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument('--out-dir', required=True)
  parser.add_argument('lists', nargs='+', help='list files or URLs')
  args = parser.parse_args()

  stats = Stats()
  lines = []
  for location in args.lists:
    lines.extend(preprocess(location))

  # $badfilter: a filter X$badfilter cancels X (uBO semantics).
  bad = set()
  for line in lines:
    pattern, options = split_options(line)
    if 'badfilter' in options:
      rest = [o for o in options if o != 'badfilter']
      bad.add(pattern + ('$' + ','.join(rest) if rest else ''))

  seen = set()
  indexed = []           # Goes to the on-device index.
  generic = []           # Generic hiding selectors, in list order.
  generic_seen = set()
  exceptions = {}        # selector -> set of domains excepting it.
  global_exceptions = set()
  for line in lines:
    if line in bad or 'badfilter' in split_options(line)[1]:
      stats.drop('$badfilter')
      continue
    converted = convert(line, stats)
    if not converted or converted in seen:
      continue
    seen.add(converted)
    for separator in ('#@#', '##'):
      pos = converted.find(separator)
      if pos >= 0:
        break
    if pos < 0:
      indexed.append(converted)  # Network rule.
      continue
    domains = [d for d in converted[:pos].split(',') if d]
    selector = converted[pos + len(separator):]
    if separator == '##' and not domains:
      if selector not in generic_seen:
        generic_seen.add(selector)
        generic.append(selector)
      continue
    if separator == '#@#':
      if not domains:
        global_exceptions.add(selector)
        continue
      exceptions.setdefault(selector, set()).update(domains)
    indexed.append(converted)  # Site-specific rule or exception.

  generic = [g for g in generic if g not in global_exceptions]
  generic_plain = [g for g in generic if g not in exceptions]
  generic_excepted = [g for g in generic if g in exceptions]

  os.makedirs(args.out_dir, exist_ok=True)
  header = ['Compiled for the Chromium iOS sideload adblock patch from:']
  header += ['  ' + location for location in args.lists]

  with open(os.path.join(args.out_dir, 'adblock_filters.txt'), 'w',
            encoding='utf-8') as f:
    f.write(''.join('! %s\n' % h for h in header))
    f.write('\n'.join(indexed) + '\n')
  with open(os.path.join(args.out_dir, 'adblock_generic.css'), 'w',
            encoding='utf-8') as f:
    f.write(''.join('/* %s */\n' % h.replace('*/', '') for h in header))
    f.write(''.join('%s{display:none!important}\n' % g for g in generic_plain))
  with open(os.path.join(args.out_dir, 'adblock_generic_exceptions.txt'), 'w',
            encoding='utf-8') as f:
    f.write(''.join('%s\t%s\n' % (','.join(sorted(exceptions[g])), g)
                    for g in generic_excepted))

  print('%s: %d indexed rules (%d network), %d generic hiding selectors '
        '(%d with per-site exceptions)' %
        (args.out_dir, len(indexed), stats.url, len(generic),
         len(generic_excepted)))
  for reason, count in sorted(stats.dropped.items(), key=lambda i: -i[1]):
    print('  skipped %6d  %s' % (count, reason))
  return 0


if __name__ == '__main__':
  sys.exit(main())

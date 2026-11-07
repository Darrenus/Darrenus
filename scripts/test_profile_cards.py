"""Catch missing README card files and renderer/README filename drift."""

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from update_cards import ROOT, render_featured


class CardReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get('src') if tag == 'img' else attrs.get('srcset') if tag == 'source' else None
        if value:
            self.paths.add(value)


class FeaturedCards(unittest.TestCase):
    def test_readme_images_exist_and_are_regenerated_without_design_changes(self):
        readme = (ROOT / 'README.md').read_text()
        featured = readme.split('<p><b>Featured work</b></p>', 1)[1].split('</td>', 1)[0]
        references = CardReferences()
        references.feed(featured)
        expected = {f'assets/{slug}-{theme}.svg'
                    for slug in ('coding-agent', 'tickwitness', 'lumen-next')
                    for theme in ('light', 'dark')}
        self.assertEqual(references.paths, expected)
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / 'assets'
            render_featured(assets)
            self.assertEqual({f'assets/{p.name}' for p in assets.glob('*.svg')}, expected)
            for reference in references.paths:
                with self.subTest(reference=reference):
                    committed = ROOT / reference
                    self.assertTrue(committed.is_file(), f'Missing README card: {reference}')
                    generated = assets / Path(reference).name
                    self.assertEqual(committed.read_bytes(), generated.read_bytes())
                    svg = ElementTree.parse(generated).getroot()
                    self.assertEqual(svg.attrib['viewBox'], '0 0 360 120')
                    texts = svg.findall('{http://www.w3.org/2000/svg}text')
                    self.assertEqual(len(texts), 4)


if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path

from knowledge.entities.ex21_structure_audit_v001 import (
    TableParser,
    norm_header,
    role_for_header,
    table_signature,
)

ROOT = Path(__file__).resolve().parents[1]


def cfg():
    return json.loads(
        (
            ROOT / "config/event_graph_ex21_structure_audit_v001.json"
        ).read_text()
    )


def test_table_parser_preserves_cells_and_spans():
    html = """
    <table>
      <tr><th>Name</th><th colspan="2">Place of Incorporation</th></tr>
      <tr><td>Acme LLC</td><td>Delaware</td><td>USA</td></tr>
    </table>
    """
    p = TableParser()
    p.feed(html)
    assert len(p.tables) == 1
    assert p.tables[0][0][0].text == "Name"
    assert p.tables[0][0][1].colspan == 2
    assert p.tables[0][1][0].text == "Acme LLC"


def test_header_roles_recognize_name_and_jurisdiction():
    c = cfg()
    assert "legal_name" in role_for_header("Name of Subsidiary", c)
    assert "jurisdiction" in role_for_header(
        "State or Jurisdiction of Organization", c
    )


def test_header_roles_recognize_dba():
    c = cfg()
    roles = role_for_header(
        "Name Under Which Doing Business Other Than Subsidiary's", c
    )
    assert "dba_alias" in roles


def test_table_signature_selects_structured_header():
    c = cfg()
    html = """
    <table>
      <tr>
        <th>Subsidiary</th>
        <th>Jurisdiction</th>
        <th>Additional Names Under Which it does Business</th>
      </tr>
      <tr><td>Acme LLC</td><td>Delaware</td><td>Acme Services</td></tr>
    </table>
    """
    p = TableParser()
    p.feed(html)
    sig = table_signature(p.tables[0], c)
    assert sig["structured_candidate"] is True
    assert set(sig["chosen_header"]["recognized_roles"]) >= {
        "legal_name", "jurisdiction", "dba_alias"
    }


def test_one_column_table_is_not_structured_candidate():
    c = cfg()
    html = "<table><tr><th>Subsidiary</th></tr><tr><td>Acme LLC</td></tr></table>"
    p = TableParser()
    p.feed(html)
    sig = table_signature(p.tables[0], c)
    assert sig["structured_candidate"] is False


def test_audit_never_creates_identity_or_graph():
    c = cfg()
    assert c["hard_guards"]["no_identity_merge"] is True
    assert c["hard_guards"]["no_canonical_entity_creation"] is True
    assert c["hard_guards"]["no_relation_promotion"] is True

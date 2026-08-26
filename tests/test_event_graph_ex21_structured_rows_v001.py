import json
from pathlib import Path

from knowledge.entities.ex21_structured_rows_v001 import (
    TableParser,infer_schema,is_footnote_table,
    uniform_formatting_span,split_trailing_footnotes,parse_ownership
)

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_ex21_structured_rows_v001.json").read_text()
    )

def table(html):
    p=TableParser();p.feed(html);return p.tables[0]

def test_explicit_chevron_synonym_is_supported():
    t=table("""
    <table>
      <tr><td>Name of Subsidiary</td>
          <td>State, Province or Country in Which Organized</td></tr>
      <tr><td>Chevron Argentina S.R.L.</td><td>Argentina</td></tr>
      <tr><td>Cabinda Gulf Oil Company Limited</td><td>Bermuda</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.family=="explicit_header"
    assert s.role_to_col["legal_name"]==0
    assert s.role_to_col["jurisdiction"]==1

def test_jpm_header_with_date_and_laws_phrase():
    t=table("""
    <table>
      <tr><td>December 31, 2024 Name</td>
          <td>Organized Under The Laws Of</td></tr>
      <tr><td>JPMorgan Chase Bank, National Association</td>
          <td>United States</td></tr>
      <tr><td>Paymentech, LLC</td><td>United States</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.role_to_col=={"legal_name":0,"jurisdiction":1}

def test_implicit_apple_left_name_column():
    t=table("""
    <table>
      <tr><td></td><td>Jurisdiction of Incorporation</td></tr>
      <tr><td>Apple Sales International</td><td>Ireland</td></tr>
      <tr><td>Apple Operations International</td><td>Ireland</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.family=="implicit_legal_name"
    assert s.role_to_col["legal_name"]==0

def test_implicit_lilly_with_spacer_column():
    t=table("""
    <table>
      <tr><td></td><td></td>
          <td>State or Jurisdiction of Incorporation or Organization</td></tr>
      <tr><td>Akouos, Inc.</td><td></td><td>Delaware</td></tr>
      <tr><td>ImClone LLC</td><td></td><td>Delaware</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.role_to_col["legal_name"]==0
    assert s.role_to_col["jurisdiction"]==2

def test_xom_implicit_name_ownership_jurisdiction():
    t=table("""
    <table>
      <tr><td></td>
          <td>Percentage of Voting Securities Owned Directly or Indirectly by Registrant</td>
          <td></td><td>State or Country of Organization</td></tr>
      <tr><td>Aera Energy LLC (5)</td><td>48.2</td><td></td><td>California</td></tr>
      <tr><td>Imperial Oil Limited</td><td>69.6</td><td></td><td>Canada</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.family=="implicit_legal_name"
    assert s.role_to_col["legal_name"]==0
    assert s.role_to_col["ownership"]==1
    assert s.role_to_col["jurisdiction"]==3

def test_footnote_prose_is_not_schema():
    t=table("""
    <table>
      <tr><td>(1)</td><td>For the purposes of this list, if the registrant owns
      directly or indirectly approximately 50 percent of the voting securities
      of any person, such person is deemed to be a subsidiary.</td></tr>
      <tr><td>(2)</td><td>With respect to certain companies, shares in names of
      nominees are included in the above percentages.</td></tr>
    </table>""")
    assert is_footnote_table(t) is True
    assert infer_schema(t,cfg(),{}) is None

def test_uniform_colspan_is_formatting():
    t=table("""
    <table>
      <tr><td colspan="3"></td>
          <td colspan="3">Jurisdiction of Incorporation</td></tr>
      <tr><td colspan="3">Apple Asia LLC</td>
          <td colspan="3">Delaware</td></tr>
      <tr><td colspan="3">Apple Canada Inc.</td>
          <td colspan="3">Canada</td></tr>
    </table>""")
    assert uniform_formatting_span(t) is True
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.arity==2

def test_walmart_dba_and_ownership_roles():
    t=table("""
    <table>
      <tr>
       <td>Subsidiary</td><td></td>
       <td>Organized or Incorporated</td><td></td>
       <td>Percent of Equity Securities Owned</td><td></td>
       <td>Name Under Which Doing Business Other Than Subsidiary's</td>
      </tr>
      <tr><td>Wal-Mart Stores Texas, LLC</td><td></td>
          <td>Delaware</td><td></td><td>100</td><td></td>
          <td>Walmart Texas</td></tr>
      <tr><td>Wal-Mart Stores Arkansas, LLC</td><td></td>
          <td>Arkansas</td><td></td><td>100</td><td></td>
          <td></td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.role_to_col["legal_name"]==0
    assert s.role_to_col["jurisdiction"]==2
    assert s.role_to_col["ownership"]==4
    assert s.role_to_col["dba_alias"]==6

def test_bac_location_is_preserved():
    t=table("""
    <table>
      <tr><td>Name</td><td>Location</td><td>Jurisdiction</td></tr>
      <tr><td>BA Continuum India Private Limited</td>
          <td>Hyderabad, India</td><td>India</td></tr>
      <tr><td>BAC Canada Finance Company</td>
          <td>Toronto, Ontario</td><td>Canada</td></tr>
    </table>""")
    s=infer_schema(t,cfg(),{})
    assert s is not None
    assert s.role_to_col["location"]==1

def test_trailing_numeric_footnotes_are_separated():
    name,refs=split_trailing_footnotes("Aera Energy LLC (4) (5)")
    assert name=="Aera Energy LLC"
    assert refs==[4,5]
    # Parentheses inside the legal name are preserved.
    name2,refs2=split_trailing_footnotes("ExxonMobil (China) Investment Co. Ltd")
    assert name2=="ExxonMobil (China) Investment Co. Ltd"
    assert refs2==[]

def test_ownership_parser_is_conservative():
    assert parse_ownership("48.2")==48.2
    assert parse_ownership("100%")==100.0
    assert parse_ownership("approximately 50")==None

def test_identity_and_graph_guards():
    c=cfg()
    assert c["hard_guards"]["no_entity_merge"] is True
    assert c["hard_guards"]["no_canonical_entity_creation"] is True
    assert c["hard_guards"]["no_relation_promotion"] is True
    assert c["hard_guards"]["no_graph_edges"] is True

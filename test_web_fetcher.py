from production.retriever.web_fetcher import extract_claims_from_google_patents_html


def test_simple_claim_heading():
    html = """
    <html><body>
    <h2>Claims (2)</h2>
    <div>1. An enzyme having at least 80% identity to SEQ ID NO:1.</div>
    <div>2. The enzyme of claim 1, wherein the enzyme is active at pH 6-8.</div>
    <h2>Priority Applications</h2>
    </body></html>
    """
    claims = extract_claims_from_google_patents_html(html)
    print(claims)
    assert "1. An enzyme having at least 80% identity" in claims
    assert "2. The enzyme of claim 1" in claims
    assert "Priority Applications" not in claims


def test_google_patents_claim_section_markup():
    """Claims and the description body are now both surfaced so the
    sequence extractor can find SEQ ID NO sequences that only appear in
    the description. Claim parsing still picks up the numbered claims
    because the description follows a clear ``--- DESCRIPTION ---`` marker
    in the combined payload.
    """
    html = """
    <html><body>
    <section itemprop="description"><div>Background text.</div></section>
    <section itemprop="claims" itemscope>
      <h2>Claims (<span itemprop="count">2</span>)</h2>
      <div itemprop="content">
        <div class="claim">
          <div id="CLM-00001" class="claim">
            <div class="claim-text"><b>1</b>. An isolated enzyme having at least 70% identity.</div>
          </div>
        </div>
        <div class="claim-dependent">
          <div id="CLM-00002" class="claim">
            <div class="claim-text"><b>2</b>. The enzyme of <claim-ref>claim 1</claim-ref>, active at pH 6-8.</div>
          </div>
        </div>
      </div>
    </section>
    <section itemprop="application"><h2>Application</h2></section>
    </body></html>
    """
    combined = extract_claims_from_google_patents_html(html)
    print(combined)
    assert "1. An isolated enzyme having at least 70% identity." in combined
    assert "2. The enzyme of claim 1, active at pH 6-8." in combined
    assert "--- DESCRIPTION ---" in combined
    assert "Background text" in combined
    # Outside-of-claims/description boilerplate (e.g. <section itemprop="application">) is dropped.
    assert "Application" not in combined


def test_description_with_sequence_listing():
    """Sequences embedded in the patent description (the common Google
    Patents shape) survive the combined-text extraction so the downstream
    sequence pipeline can compare them to the product sequence."""
    html = """
    <html><body>
    <section itemprop="claims" itemscope>
      <h2>Claims (1)</h2>
      <div class="claim">1. An enzyme having at least 80% identity to SEQ ID NO:1.</div>
    </section>
    <section itemprop="description">
      <div class="description-paragraph">
        <p>The amino acid sequence of SEQ ID NO:1 is shown below:</p>
        <p>MKTAYIAKQRQISFVKSHFSRQEILDLICGYRAQHGEVPDSLLITKE</p>
      </div>
    </section>
    </body></html>
    """
    combined = extract_claims_from_google_patents_html(html)
    from production.sequence.analysis import extract_reference_sequences
    sequences = extract_reference_sequences(combined)
    assert sequences.get("SEQ ID NO:1") == "MKTAYIAKQRQISFVKSHFSRQEILDLICGYRAQHGEVPDSLLITKE", sequences


def main():
    test_simple_claim_heading()
    test_google_patents_claim_section_markup()
    test_description_with_sequence_listing()


if __name__ == "__main__":
    main()

from pr_swipe.audit import AuditLog, verify

def test_chain_verifies_and_detects_tampering(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = AuditLog(p)
    log.append("close", repo="o/r", number=1)
    log.append("merge", repo="o/r", number=2, status=409)
    assert verify(p)
    AuditLog(p).append("merge", repo="o/r", number=3)  # reopened log continues the chain
    assert verify(p)
    lines = p.read_text().splitlines()
    p.write_text("\n".join([lines[0], lines[1].replace("409", "200"), lines[2]]) + "\n")
    assert not verify(p)

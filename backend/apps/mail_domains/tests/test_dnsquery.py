"""Klient DNS na bibliotece standardowej (MAIL-01 § 4.1): pakiety składane ręcznie, bez sieci."""

from __future__ import annotations

import struct

import pytest

from apps.mail_domains import dnsquery
from apps.mail_domains.dnsquery import TYPE_A, TYPE_MX, TYPE_TXT, DnsError, Resolver


def _rr_name_pointer(offset: int = 12) -> bytes:
    return struct.pack("!H", 0xC000 | offset)


def response(
    qid: int, name: str, qtype: int, answers: list[tuple[int, bytes]], *, flags: int = 0x8180
) -> bytes:
    """Odpowiedź z pytaniem i rekordami, których nazwa to wskaźnik na nazwę z pytania (offset 12)."""
    header = struct.pack("!HHHHHH", qid, flags, 1, len(answers), 0, 0)
    question = dnsquery.encode_name(name) + struct.pack("!HH", qtype, 1)
    body = b""
    for rtype, rdata in answers:
        body += _rr_name_pointer() + struct.pack("!HHIH", rtype, 1, 300, len(rdata)) + rdata
    return header + question + body


def txt_rdata(*parts: bytes) -> bytes:
    return b"".join(bytes([len(part)]) + part for part in parts)


def test_query_packet_has_rd_bit_question_and_edns0():
    packet = dnsquery.build_query(0x1234, "iqo-official.org", TYPE_TXT)
    qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", packet[:12])
    assert (qid, flags, qd, an, ns, ar) == (0x1234, 0x0100, 1, 0, 0, 1)
    assert b"\x0ciqo-official\x03org\x00" in packet
    assert packet.endswith(b"\x00" + struct.pack("!HHIH", 41, 4096, 0, 0))


def test_txt_split_into_strings_is_joined_without_separator():
    # Klucz DKIM 2048 bitów nie mieści się w jednym napisie (255 B) – panele dzielą go na kilka.
    message = response(
        7, "olimpiada._domainkey.iqo.test", TYPE_TXT, [(TYPE_TXT, txt_rdata(b"v=DKIM1; p=AAA", b"BBB"))]
    )
    answer = dnsquery.parse_response(message, 7)
    assert answer.records == [(TYPE_TXT, "v=DKIM1; p=AAABBB")]


def test_a_and_mx_records_with_compressed_names():
    exchange = b"\x05aspmx\x01l" + _rr_name_pointer(12 + len(b"\x03iqo"))  # wskaźnik na „test”
    message = response(
        9,
        "iqo.test",
        TYPE_MX,
        [(TYPE_MX, struct.pack("!H", 5) + exchange), (TYPE_A, bytes([169, 58, 242, 197]))],
    )
    answer = dnsquery.parse_response(message, 9)
    assert answer.records[0] == (TYPE_MX, (5, "aspmx.l.test"))
    assert answer.records[1] == (TYPE_A, "169.58.242.197")


def test_nxdomain_is_an_empty_answer_not_an_error():
    message = response(3, "nope.iqo.test", TYPE_TXT, [], flags=0x8183)
    answer = dnsquery.parse_response(message, 3)
    assert answer.nxdomain and answer.records == []


@pytest.mark.parametrize(
    ("message", "qid", "fragment"),
    [
        (response(1, "a.test", TYPE_TXT, [], flags=0x8182), 1, "kodem błędu 2"),
        (response(1, "a.test", TYPE_TXT, []), 2, "identyfikator"),
        (response(1, "a.test", TYPE_TXT, [], flags=0x0100), 1, "nie jest odpowiedzią"),
        (b"\x00\x01", 1, "krótsza"),
    ],
)
def test_broken_or_failed_answers_raise(message, qid, fragment):
    with pytest.raises(DnsError, match=fragment):
        dnsquery.parse_response(message, qid)


def test_pointer_loop_does_not_hang():
    message = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0) + _rr_name_pointer(12)
    with pytest.raises(DnsError, match="pętla"):
        dnsquery.parse_response(message, 1)


def test_invalid_label_is_rejected():
    with pytest.raises(DnsError):
        dnsquery.encode_name("a" * 64 + ".test")


def test_nameservers_from_resolv_conf(tmp_path):
    conf = tmp_path / "resolv.conf"
    conf.write_text("# komentarz\nsearch local\nnameserver 127.0.0.11\nnameserver zly\nnameserver ::1\n")
    assert dnsquery.default_nameservers(str(conf)) == ["127.0.0.11", "::1"]
    assert dnsquery.default_nameservers(str(tmp_path / "brak")) == list(dnsquery.FALLBACK_NAMESERVERS)


def test_truncated_udp_answer_is_repeated_over_tcp(monkeypatch):
    calls = []

    def fake_udp(self, server, packet):
        calls.append("udp")
        qid = struct.unpack("!H", packet[:2])[0]
        return response(qid, "big.test", TYPE_TXT, [], flags=0x8380)  # TC=1

    def fake_tcp(self, server, packet):
        calls.append("tcp")
        qid = struct.unpack("!H", packet[:2])[0]
        return response(qid, "big.test", TYPE_TXT, [(TYPE_TXT, txt_rdata(b"v=spf1 -all"))])

    monkeypatch.setattr(Resolver, "_udp", fake_udp)
    monkeypatch.setattr(Resolver, "_tcp", fake_tcp)
    assert Resolver(["192.0.2.1"]).txt("big.test") == ["v=spf1 -all"]
    assert calls == ["udp", "tcp"]


def test_no_answer_from_any_server_raises_after_retries(monkeypatch):
    attempts = []

    def silent(self, server, packet):
        attempts.append(server)
        raise TimeoutError("timed out")

    monkeypatch.setattr(Resolver, "_udp", silent)
    with pytest.raises(DnsError, match="brak odpowiedzi"):
        Resolver(["192.0.2.1", "192.0.2.2"], tries=2).a("x.test")
    assert attempts == ["192.0.2.1", "192.0.2.2", "192.0.2.1", "192.0.2.2"]

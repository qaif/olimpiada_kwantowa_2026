# Rekordy DNS – olimpiadakwantowa.pl (do wklejenia w panelu home.pl)

Wartości 1:1 z `deploy/dns-olimpiadakwantowa.pl.zone`. TTL: 3600 (domyślny). Rekordy A dla `@` i `www` już istnieją.

| Typ | Nazwa (host) | Wartość | Po co |
|---|---|---|---|
| A | `@` | `169.58.242.197` | strona (istnieje) |
| A | `www` | `169.58.242.197` | przekierowanie na domenę główną (istnieje) |
| A | `s3` | `169.58.242.197` | pliki (presigned URL) przez `s3.olimpiadakwantowa.pl` zamiast `:9000` |
| A | `mail` | `169.58.242.197` | nazwa serwera poczty (HELO) |
| A | `meet` | `169.58.242.197` | własne Jitsi Meet do rozmów kwalifikacyjnych (`scripts/deploy_jitsi.sh`) |
| TXT | `@` | `v=spf1 ip4:169.58.242.197 -all` | SPF |
| TXT | `olimpiada._domainkey` | `v=DKIM1; h=sha256; k=rsa; s=email; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAotteu3GhRRsJajATnJVGe7ThN+Er4hCMcNx6vKIx0hEJeNDVTok4OwaV6yHXaU403ku7YqufNTP9OdpbfpgSrJMI4l3wUB2796NNhA0zJDP7WnLS7juPsPfDYzxZXqtU6oC+PdzkqUcBHz67gCTJzKtU7wU+OPv5be883gYJRduor4OnAv5ZSeaMZ1eUEViQWgcQrpqXMEDhgkUStlHsgXpngQIxsSKF3mPhuAn6G2PxsCR0HGl8gRhk0SwiVc0Ak819do1ItjB0UmJwkV/nufoqyWUppmKEtTgsZp63NaJU3fZfqaRXbwBiS1tpRknvV+OaGkN6hd66uTkiWVGjhwIDAQAB` | DKIM |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:contact@qaif.org; adkim=r; aspf=r; fo=1` | DMARC |
| CAA | `@` | `0 issue "letsencrypt.org"` | tylko Let's Encrypt wystawia certyfikaty (opcjonalne) |

Poza strefą, w panelu **Contabo** (Reverse DNS): `169.58.242.197` → `mail.olimpiadakwantowa.pl`.

## Uwagi do panelu home.pl
- W polu „nazwa” wpisuj tylko część przed domeną (`mail`, `s3`, `_dmarc`, `olimpiada._domainkey`); dla `@` zostaw puste lub wybierz domenę główną.
- Wartość TXT DKIM wklej w całości, bez łamania linii i bez dodatkowych cudzysłowów, jeśli panel dodaje je sam.
- Propagacja: do 24 h (zwykle minuty). Sprawdzenie: `nslookup -type=TXT olimpiada._domainkey.olimpiadakwantowa.pl`.

## Po propagacji (zrobi Claude / deploy)
1. `s3`: w `/opt/olimpiada/.env` `S3_PUBLIC_ADDRESS=s3.olimpiadakwantowa.pl`, `S3_PUBLIC_ENDPOINT_URL=https://s3.olimpiadakwantowa.pl`, `docker compose up -d proxy web worker`; port 9000 można wtedy zamknąć w ufw.
2. Poczta: test na `check-auth@verifier.port25.com` – w odpowiedzi `SPF check: pass`, `DKIM check: pass`.

# GitLab CE pod `gitlab.<domena>`

GitLab stoi na tym samym serwerze co portal, ale w **osobnym projekcie compose** (`/opt/gitlab`), tak
jak Jitsi. Deploy portalu (`scripts/deploy.sh`) go nie dotyka. Ze strony portalu są trzy rzeczy:

- blok `gitlab.{$SITE_DOMAIN}` w `deploy/Caddyfile`: HTTPS (Let's Encrypt) i `reverse_proxy gitlab:80`,
  przypięcie TLS przy `PLATFORM_SUBDOMAINS=1` w `scripts/render_caddyfile.sh`,
- sieć `gitlab_front` w `docker-compose.yml` (`internal`, `172.30.16.0/24`), w której z usług
  portalu stoi tylko `proxy`. Nie jest to `edge`, bo jej adresom aplikacja ufa (`TRUSTED_PROXY_IPS`),
- `gitlab` na liście zarezerwowanych slugów konkursów (`competition_create_forms.py`) i hostów
  infrastruktury (`apps/notebooks/checks.py`).

Bez GitLaba na serwerze blok czeka na certyfikat albo oddaje 502, a w sieci `gitlab_front` stoi sam
Caddy. Nic więcej się nie dzieje.

## Instalacja

1. Rekord DNS `gitlab.<domena>` na adres serwera (CNAME na domenę główną wystarczy).
2. Wdrożenie portalu z tą zmianą. Zakłada ono sieć `olimpiada_gitlab_front` i podłącza do niej proxy.
3. Na serwerze:

   ```bash
   mkdir -p /opt/gitlab && cp /opt/olimpiada/deploy/gitlab/docker-compose.gitlab.yml /opt/gitlab/
   cd /opt/gitlab && docker compose -f docker-compose.gitlab.yml up -d
   ```

   Pierwszy start trwa kilka minut (`docker inspect gitlab --format '{{.State.Health.Status}}'`).
   Inna domena: `GITLAB_HOST=gitlab.example.org` w `/opt/gitlab/.env`.
4. Hasło `root`: `grep Password: /opt/gitlab/config/initial_root_password`. Plik znika sam po 24 h,
   więc zmień hasło przy pierwszym logowaniu. Potem w Admin → Settings → General → Sign-up restrictions
   wyłącz samodzielną rejestrację.

Git po SSH: `ssh://git@gitlab.<domena>:2222/<grupa>/<repo>.git`.

## Aktualizacja

GitLab wymaga przechodzenia przez kolejne **przystanki aktualizacji** (upgrade path), nie da się
przeskoczyć dowolnej liczby wersji. Ścieżkę sprawdź w narzędziu
<https://gitlab-com.gitlab.io/support/toolbox/upgrade-path/>. Przed każdym krokiem zrób kopię:

```bash
docker exec -t gitlab gitlab-backup create
cp -a /opt/gitlab/config /opt/gitlab/config.bak-$(date +%F)   # gitlab-secrets.json nie wchodzi do kopii
```

Następnie `GITLAB_VERSION=<wersja>-ce.0` w `/opt/gitlab/.env` i `docker compose -f docker-compose.gitlab.yml up -d`.

## Pamięć

Limit kontenera to 4 GiB (`GITLAB_MEM_LIMIT`). Konfiguracja jest w trybie oszczędnym (Puma bez
workerów, Sidekiq 10, bez Prometheusa), więc GitLab zajmuje ok. 2,7 GiB. Serwer dzieli z Jitsi i
LiveKitem, dlatego przy dużym wydarzeniu na żywo warto GitLaba zatrzymać:
`docker compose -f docker-compose.gitlab.yml stop`.

## Poczta

Nie jest skonfigurowana. GitLab nie wysyła powiadomień ani resetów hasła. Relay `mail` portalu stoi w
sieci `internal` z listą `mynetworks`, do której GitLab celowo nie należy. Pocztę GitLaba podłącza się
przez zewnętrzny SMTP (`gitlab_rails['smtp_*']` w `GITLAB_OMNIBUS_CONFIG`).

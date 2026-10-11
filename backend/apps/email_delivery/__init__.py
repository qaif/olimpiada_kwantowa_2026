"""Doręczalność poczty (MAIL-02): literówki w adresach przy wpisywaniu i odbicia (bounce) po wysyłce.

Dwie połowy jednej sprawy – „list nie dotarł, bo adres jest zły”:

- **zanim** adres trafi do bazy: podpowiedź „Czy chodziło Ci o …?” (``typos``) i twarda blokada
  domen, które na pewno nie przyjmują poczty (``dnscheck``) – pole ``fields.CheckedEmailField``,
- **po** wysyłce: odmowy relaya (``backends``) i zawiadomienia o niedoręczeniu ze skrzynki Maildir
  relaya (``bounces``, ``tasks``) zapisane w ``models.DeliveryStatus``, baner dla właściciela adresu,
  lista koordynatora i wstrzymanie listów nieobowiązkowych (``services``).

Specyfikacja: ``docs/tasks/MAIL-02.md``; operator: ``docs/OPERACJE.md`` § 52.
"""

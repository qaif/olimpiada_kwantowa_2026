# Zadania przykładowe

Cztery zadania o rosnącej trudności pokazują, czego można się spodziewać w Olimpiadzie Kwantowej: od rachunku na jednym kubicie po dowód, którego nie zmieści się na jednej kartce. Nie są to zadania żadnego etapu – służą do treningu i do sprawdzenia, jak wygląda oczekiwany zapis rozwiązania.

> Zadania są materiałem przygotowawczym opracowanym na potrzeby portalu. Zadania konkursowe układa Komitet Merytoryczny i publikuje je wyłącznie w zakładce „Zadania” po otwarciu etapu.

## Jak czytać i jak zapisywać rozwiązanie

- Każde zadanie ma kilka podpunktów; w zawodach zadanie ocenia się w skali 0 / 2 / 5 / 6 punktów opisanej w [Zasadach Organizacji Zawodów](/dokumenty/zoz/). Ocenie podlega tok rozumowania, nie sam wynik.
- Używamy notacji Diraca: stan kubitu to |ψ⟩ = α|0⟩ + β|1⟩ z warunkiem |α|² + |β|² = 1, a prawdopodobieństwo wyniku 0 w pomiarze w bazie obliczeniowej wynosi |α|².
- Stany dwóch kubitów zapisujemy jako |ab⟩ = |a⟩ ⊗ |b⟩; pierwszy symbol dotyczy pierwszego kubitu.
- Odpowiedzi i szkice rozwiązań są na końcu strony. Najpierw spróbuj samodzielnie.

## Zadanie 1 (proste): stan kubitu i pomiar

Kubit jest w stanie |ψ⟩ = (1/√3)|0⟩ + a|1⟩, gdzie a jest liczbą rzeczywistą dodatnią.

- **a)** Wyznacz a.
- **b)** Jakie jest prawdopodobieństwo otrzymania wyniku 1 w pomiarze w bazie obliczeniowej {|0⟩, |1⟩}?
- **c)** Na kubit działa bramka X (kwantowy NOT), która zamienia |0⟩ ↔ |1⟩. Zapisz stan po bramce i podaj prawdopodobieństwo wyniku 0.
- **d)** Czy stany |ψ⟩ oraz −|ψ⟩ (ten sam stan pomnożony przez −1) można odróżnić jakimkolwiek pomiarem? Uzasadnij.

## Zadanie 2 (średnie): splątanie z dwóch bramek

Dwa kubity startują w stanie |00⟩. Na pierwszy kubit działa bramka Hadamarda H: H|0⟩ = (|0⟩ + |1⟩)/√2 oraz H|1⟩ = (|0⟩ − |1⟩)/√2. Następnie na oba kubity działa bramka CNOT z pierwszym kubitem jako sterującym: |x y⟩ → |x, y ⊕ x⟩, gdzie ⊕ to dodawanie modulo 2.

- **a)** Zapisz stan układu po bramce H, a potem po bramce CNOT.
- **b)** Podaj prawdopodobieństwa czterech możliwych wyników pomiaru obu kubitów w bazie obliczeniowej. Co wiadomo o drugim kubicie, jeżeli pomiar pierwszego dał 1?
- **c)** Wykaż, że stanu końcowego nie da się zapisać jako iloczynu (α|0⟩ + β|1⟩) ⊗ (γ|0⟩ + δ|1⟩) dla żadnych liczb α, β, γ, δ. Taki stan nazywamy splątanym.
- **d)** Powtórz obliczenie dla stanu początkowego |10⟩. Czy pomiar obu kubitów w bazie obliczeniowej pozwala odróżnić otrzymany stan od stanu z podpunktu a)? Zaproponuj pomiar, który je odróżni.

## Zadanie 3 (trudne): podsłuch w protokole BB84

Alicja wysyła Bobowi fotony, kodując bit 0 lub 1 w jednej z dwóch baz: prostej {|0⟩, |1⟩} albo ukośnej {|+⟩, |−⟩}, gdzie |±⟩ = (|0⟩ ± |1⟩)/√2. Bazę wybiera losowo dla każdego fotonu. Bob mierzy każdy foton w losowo wybranej bazie. Po transmisji oboje ogłaszają publicznie użyte bazy (ale nie wyniki) i zachowują tylko bity, dla których bazy się zgodziły – to klucz surowy.

Ewa przechwytuje każdy foton, mierzy go w losowo wybranej bazie i wysyła Bobowi nowy foton w stanie, który zmierzyła (atak „przechwyć i wyślij”).

- **a)** Pokaż, że jeśli Ewa wybrała inną bazę niż Alicja, to bit Boba – w sytuacji, gdy Bob użył tej samej bazy co Alicja – jest błędny z prawdopodobieństwem 1/2.
- **b)** Wyznacz prawdopodobieństwo, że losowy bit klucza surowego jest błędny (tzw. QBER).
- **c)** Alicja i Bob ujawniają publicznie n losowo wybranych bitów klucza surowego i porównują je. Podaj prawdopodobieństwo, że podsłuch Ewy pozostanie niewykryty. Ile bitów muszą porównać, aby wykryć Ewę z prawdopodobieństwem co najmniej 0,999?
- **d)** Ewa przechwytuje tylko ułamek f wszystkich fotonów. Wyznacz QBER oraz ułamek bitów klucza surowego, które Ewa zna z pewnością. Oblicz obie wielkości dla f = 0,2.

## Zadanie 4 (diabelnie trudne): nierówność CHSH i granica Tsirelsona

Alicja i Bob otrzymują po jednej cząstce z pary. Alicja wybiera jeden z dwóch pomiarów, A lub A′, Bob – B lub B′; każdy pomiar daje wynik +1 albo −1. Dla wielu powtórzeń definiujemy korelację E(A, B) jako wartość średnią iloczynu wyników oraz wielkość

S = E(A, B) + E(A, B′) + E(A′, B) − E(A′, B′).

- **a)** Załóż, że wyniki są z góry ustalone przez ukryte parametry: w każdym powtórzeniu wszystkie cztery wielkości A, A′, B, B′ mają określone wartości ±1 (także te niezmierzone). Wykaż, że wtedy AB + AB′ + A′B − A′B′ = ±2, i wywnioskuj nierówność CHSH: |S| ≤ 2.
- **b)** Para jest w stanie singletowym |Ψ⁻⟩ = (|01⟩ − |10⟩)/√2, a pomiar „w kierunku” o kącie a w ustalonej płaszczyźnie opisuje obserwabla σ(a) = cos(a)·Z + sin(a)·X, gdzie Z i X to macierze Pauliego. Przyjmij bez dowodu, że E(a, b) = −cos(a − b). Dobierz kąty a, a′, b, b′ tak, aby |S| = 2√2, i sprawdź, że nierówność z podpunktu a) jest złamana.
- **c)** Udowodnij wzór E(a, b) = −cos(a − b), obliczając ⟨Ψ⁻| σ(a) ⊗ σ(b) |Ψ⁻⟩. Wskazówka: najpierw pokaż, że (Z ⊗ Z)|Ψ⁻⟩ = −|Ψ⁻⟩ oraz (X ⊗ X)|Ψ⁻⟩ = −|Ψ⁻⟩, a wyrazy mieszane Z ⊗ X i X ⊗ Z mają wartość oczekiwaną zero.
- **d)** Wykaż, że 2√2 jest największą wartością |S| dopuszczaną przez mechanikę kwantową (granica Tsirelsona). Wskazówka: dla operatorów A, A′, B, B′ o wartościach własnych ±1 (czyli A² = A′² = B² = B′² = I), z których operatory Alicji komutują z operatorami Boba, rozważ C = A ⊗ (B + B′) + A′ ⊗ (B − B′) i oblicz C². Skorzystaj z tego, że norma komutatora dwóch takich operatorów nie przekracza 2.

## Odpowiedzi i szkice rozwiązań

### Zadanie 1

- **a)** Z normalizacji 1/3 + a² = 1, więc a = √(2/3) = √6/3.
- **b)** P(1) = a² = 2/3.
- **c)** X|ψ⟩ = (1/√3)|1⟩ + √(2/3)|0⟩, więc P(0) = 2/3.
- **d)** Nie. Prawdopodobieństwa wszystkich wyników zależą od kwadratów modułów amplitud (ogólniej: od |⟨φ|ψ⟩|² dla dowolnego stanu |φ⟩), a te są takie same dla |ψ⟩ i −|ψ⟩. Globalny czynnik fazowy nie ma znaczenia fizycznego.

### Zadanie 2

- **a)** Po H: (|00⟩ + |10⟩)/√2. Po CNOT: (|00⟩ + |11⟩)/√2 – stan Bella |Φ⁺⟩.
- **b)** P(00) = P(11) = 1/2, P(01) = P(10) = 0. Jeżeli pierwszy kubit dał 1, drugi na pewno da 1 – wyniki są idealnie skorelowane.
- **c)** Iloczyn ma amplitudy αγ, αδ, βγ, βδ przy |00⟩, |01⟩, |10⟩, |11⟩. Warunki αδ = 0 i βγ = 0 wymuszają, że α = 0 lub δ = 0 oraz β = 0 lub γ = 0; w każdym przypadku zeruje się też αγ albo βδ, a te muszą wynosić 1/√2. Sprzeczność.
- **d)** Dla |10⟩: po H mamy (|00⟩ − |10⟩)/√2, po CNOT (|00⟩ − |11⟩)/√2 = |Φ⁻⟩. W bazie obliczeniowej rozkład wyników jest identyczny jak dla |Φ⁺⟩ (1/2 na 00 i 11). Odróżnia je pomiar obu kubitów w bazie {|+⟩, |−⟩}: |Φ⁺⟩ = (|++⟩ + |−−⟩)/√2 daje zawsze zgodne wyniki, a |Φ⁻⟩ = (|+−⟩ + |−+⟩)/√2 zawsze przeciwne.

### Zadanie 3

- **a)** Gdy bazy Ewy i Alicji są różne, wynik Ewy jest losowy (każdy stan jednej bazy ma w drugiej bazie prawdopodobieństwa 1/2 i 1/2), a foton wysłany do Boba jest w stanie z „obcej” bazy. Bob, mierząc w bazie Alicji, dostaje wynik losowy – błędny z prawdopodobieństwem 1/2.
- **b)** W kluczu surowym bazy Alicji i Boba są zgodne. Ewa trafia w bazę z prawdopodobieństwem 1/2 (wtedy błędu nie ma) i chybia z prawdopodobieństwem 1/2 (wtedy błąd z prawdopodobieństwem 1/2). QBER = 1/2 · 1/2 = 1/4.
- **c)** Każdy porównany bit zgadza się z prawdopodobieństwem 3/4, więc Ewa pozostaje niewykryta z prawdopodobieństwem (3/4)ⁿ. Warunek (3/4)ⁿ ≤ 0,001 daje n ≥ ln(0,001)/ln(0,75) ≈ 24,01, czyli n = 25 bitów.
- **d)** QBER = f/4. Ewa zna bit na pewno wtedy, gdy przechwyciła foton i trafiła w bazę: ułamek f/2 bitów klucza surowego. Dla f = 0,2: QBER = 0,05 (5 %), Ewa zna 10 % bitów. Wniosek: sama obserwacja QBER nie wystarcza – dlatego po ustaleniu klucza surowego stosuje się korekcję błędów i wzmocnienie prywatności.

### Zadanie 4

- **a)** AB + AB′ + A′B − A′B′ = A(B + B′) + A′(B − B′). Skoro B, B′ = ±1, to jedna z sum B + B′, B − B′ wynosi 0, a druga ±2, więc całe wyrażenie równa się ±2. Wartość średnia po wielu powtórzeniach (po dowolnym rozkładzie ukrytych parametrów) leży wtedy w przedziale [−2, 2]: |S| ≤ 2.
- **b)** Na przykład a = 0, a′ = π/2, b = π/4, b′ = −π/4. Wtedy E(a,b) = E(a,b′) = E(a′,b) = −√2/2, a E(a′,b′) = −cos(3π/4) = +√2/2, więc S = −3·√2/2 − √2/2 = −2√2 i |S| = 2√2 > 2.
- **c)** Bezpośrednim rachunkiem: (Z ⊗ Z)|01⟩ = −|01⟩ i (Z ⊗ Z)|10⟩ = −|10⟩, więc (Z ⊗ Z)|Ψ⁻⟩ = −|Ψ⁻⟩; (X ⊗ X) zamienia |01⟩ ↔ |10⟩, co daje (X ⊗ X)|Ψ⁻⟩ = −|Ψ⁻⟩. Wyrazy Z ⊗ X i X ⊗ Z przeprowadzają |Ψ⁻⟩ w kombinacje |00⟩ i |11⟩, ortogonalne do |Ψ⁻⟩, więc ich wartość oczekiwana znika. Stąd ⟨σ(a) ⊗ σ(b)⟩ = −cos a cos b − sin a sin b = −cos(a − b).
- **d)** Korzystając z A² = A′² = B² = B′² = I oraz z komutowania operatorów Alicji z operatorami Boba, otrzymujemy C² = 4·I − [A, A′] ⊗ [B, B′], gdzie [P, Q] = PQ − QP. Dla operatorów o normie 1 zachodzi ‖[P, Q]‖ ≤ 2, więc ‖C²‖ ≤ 4 + 4 = 8 i ‖C‖ ≤ 2√2. Ponieważ S jest wartością oczekiwaną C w pewnym stanie, |S| ≤ ‖C‖ ≤ 2√2. Wartość ta jest osiągana w podpunkcie b), więc granica jest ścisła.

## Co dalej

- Terminy etapów: [Harmonogram](/harmonogram/). Tematy i terminy warsztatów przygotowawczych: [Warsztaty](/warsztaty/).
- Zasady oceniania, dozwolone narzędzia i format przesyłanych rozwiązań: [Zasady Organizacji Zawodów](/dokumenty/zoz/).

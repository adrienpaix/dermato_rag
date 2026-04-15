from langchain_google_genai import ChatGoogleGenerativeAI
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.messages import HumanMessage, SystemMessage
from typing import List, Dict, Any, Tuple, Optional
from ..config import config

SECTIONS = [
    ("Épidémiologie et Introduction", "épidémiologie incidence prévalence facteurs de risque définition"),
    ("Présentation Clinique", "présentation clinique signes symptômes aspect macroscopique lésion clinique"),
    ("Diagnostic", "diagnostic critères histologie anatomopathologie bilan dermoscopie staging classification"),
    ("Traitement", "traitement topique dermocorticoïde vitamine D photothérapie biothérapie immunosuppresseur protocole algoritme"),
    ("Surveillance et Suivi", "surveillance suivi récidive réponse toxicité effets secondaires qualité de vie"),
]

DETAIL_LEVELS = {
    "court": "300-500 mots",
    "standard": "400-600 mots",
    "exhaustif": "1200-2000 mots"
}

TONES = {
    "academique": "DES",
    "ecn": "bullet points courts",
    "clinicien": "pragmatique"
}

SYSTEM_PROMPT_BASE = """Tu es un dermatologue expert de niveau hospitalo-universitaire, auteur de cours de DES (Diplôme d'Études Spécialisées) de Dermatologie.
Tu rédiges un cours académique complet, rigoureux, exhaustif, de niveau DES.

Règles absolues :
- AUCUNE restriction de longueur — chaque section doit être complète et détaillée (minimum 400 mots)
- Utilise IMPÉRATIVEMENT les informations des chunks fournis
- Cite les sources dans le texte sous la forme (Auteur et al., Année) ou (Auteur, Année)
- Écris en français médical académique, avec la terminologie exacte
- Structure avec des sous-sections (##, ###) et des listes quand approprié
- Ne jamais écrire "je ne peux pas" ou raccourcir le contenu
- Le cours doit être directement utilisable pour la préparation au DES
"""

EBM_PROMPT_TREATMENT = """Pour CHAQUE traitement ou ligne thérapeutique mentionné, tu DOIS :
1. Citer l'essai clinique de référence uniquement s'il est explicitement présent dans les chunks fournis (nom de l'essai, auteurs principaux, année, journal)
2. Donner le design de l'étude (phase, randomisé/non, N patients, population) — uniquement si les données sont dans les chunks
3. Citer l'endpoint primaire et le résultat chiffré (HR, OR, PFS médiane, OS médiane, taux de réponse) — UNIQUEMENT si présents dans les chunks
4. Donner le niveau de preuve (Grade A/B/C selon HAS ou niveau 1/2/3)
5. Mentionner les limites de l'étude si pertinent

RÈGLE ABSOLUE : Tu ne dois JAMAIS invent er, inventer ou générer un essai clinique qui ne figure pas explicitement dans les chunks fournis. Si les données d'un essai ne sont pas dans les chunks, écris : "Essai : aucune donnée disponible dans les sources" — ne JAMAIS créer de fausse citation.

Format obligatoire pour chaque traitement :
**[Nom traitement]** — [Indication précise]
> Essai : [Nom] ([Auteurs] [Année]) — [Journal] — UNQUEMENT si présent dans les chunks, sinon : "non retrouvé dans les sources"
> Design : [Phase X, N=XXX, population] — UNQUEMENT si dans les chunks
> Résultat : [endpoint primaire] : [chiffre] (HR=X.XX [IC95%], p=X.XX) — UNIQUEMENT si dans les chunks
> Niveau de preuve : [Grade/Niveau]"""


class CourseGenerator:
    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or config.GOOGLE_API_KEY
        self.model_name = model
        self.llm = ChatGoogleGenerativeAI(
            model=self.model_name,
            google_api_key=self.api_key,
            temperature=0.3,
            max_output_tokens=4096,
        )

    def _extract_citation_key(self, source: str) -> str:
        """Extrait le nom de fichier comme clé de citation"""
        import re
        name = source.replace('.pdf', '').replace('_', ' ')
        # Try to extract author + year
        match = re.search(r'([A-Z][a-z]+(?:\s+et\s+al\.?)?).*?(\d{4})', name)
        if match:
            return f"{match.group(1)}, {match.group(2)}"
        return name[:50]

    def _build_system_prompt(self, no_redundancy: bool, focus_treatment_evidence: bool, section_title: str) -> str:
        """Construit le prompt système en fonction des options"""
        prompt = SYSTEM_PROMPT_BASE
        if no_redundancy:
            prompt += "\n\nIMPÉRATIF: ne répète JAMAIS une information déjà mentionnée dans une section précédente. Si tu dois référencer un point déjà traité, cite simplement 'cf. section X' sans le redévelopper."
        if focus_treatment_evidence and section_title == "Traitement":
            prompt += "\n\n" + EBM_PROMPT_TREATMENT
        return prompt

    def _build_section_prompt(self, section_title: str, chunks: List[Dict], course_title: str, level: str,
                              detail_level: str, tone: str, extra_instructions: str,
                              no_redundancy: bool, focus_treatment_evidence: bool,
                              include_key_points: bool, include_clinical_cases: bool, include_qcm: bool) -> str:
        """Construit le prompt complet pour une section"""
        context = ""
        for i, chunk in enumerate(chunks[:15]):
            source = chunk.get('source', 'Source inconnue')
            text = chunk.get('text', '')
            citation_key = self._extract_citation_key(source)
            context += f"\n[Source {i+1}: {citation_key}]\n{text}\n"

        system_prompt = self._build_system_prompt(no_redundancy, focus_treatment_evidence, section_title)
        
        detail_desc = DETAIL_LEVELS.get(detail_level, "600-900 mots")
        tone_desc = TONES.get(tone, "DES")
        
        prompt = f"""
{system_prompt}

Cours : {course_title}
Section à rédiger : {section_title}
Niveau : {level}
Niveau de détail : {detail_desc}
Ton : {tone_desc}
Instructions supplémentaires : {extra_instructions}

DONNÉES DE LA LITTÉRATURE À INTÉGRER :
{context}

Rédige maintenant la section "{section_title}" de manière exhaustive et complète.
Utilise les données ci-dessus et cite les sources dans le texte.
Commence directement par le contenu (pas de préambule).
"""
        # Ajouter des instructions pour les points clés, cas cliniques, QCM si demandés
        # Ces sections seront ajoutées après la génération principale via des appels séparés
        return prompt

    def generate_section(self, section_title: str, chunks: List[Dict], course_title: str, level: str = "DES",
                         sections_enabled: Optional[List[str]] = None,
                         detail_level: str = "standard",
                         tone: str = "academique",
                         extra_instructions: str = "",
                         no_redundancy: bool = True,
                         focus_treatment_evidence: bool = True,
                         include_key_points: bool = False,
                         include_clinical_cases: bool = False,
                         include_qcm: bool = False) -> str:
        """Génère une section avec les paramètres avancés"""
        # Si sections_enabled est fourni et que cette section n'est pas dedans, retourner chaîne vide
        if sections_enabled is not None and section_title not in sections_enabled:
            return ""
        
        prompt = self._build_section_prompt(section_title, chunks, course_title, level,
                                           detail_level, tone, extra_instructions,
                                           no_redundancy, focus_treatment_evidence,
                                           include_key_points, include_clinical_cases, include_qcm)
        
        messages = [HumanMessage(content=prompt)]
        response = self.llm.invoke(messages)
        content = response.content
        
        # Ajouter les points clés si demandé
        if include_key_points:
            key_points = self._generate_key_points(section_title, content, course_title)
            content += "\n\n" + key_points
        
        # Les cas cliniques et QCM sont gérés au niveau du cours complet, pas par section
        return content

    def _generate_key_points(self, section_title: str, section_content: str, course_title: str) -> str:
        """Génère un encadré Points Clés avec 5 bullets"""
        prompt = f"""
Tu as rédigé la section "{section_title}" du cours "{course_title}".
Extrais-en les 5 points les plus importants sous forme de bullet points concis.
Format :
## Points clés
- Point 1
- Point 2
- Point 3
- Point 4
- Point 5

Ne rajoute pas d'explications supplémentaires.
"""
        messages = [HumanMessage(content=prompt)]
        response = self.llm.invoke(messages)
        return response.content

    def _generate_clinical_cases(self, course_title: str, all_chunks: List[Dict]) -> str:
        """Génère 2 cas cliniques avec Q&A"""
        context = ""
        for i, chunk in enumerate(all_chunks[:20]):
            source = chunk.get('source', 'Source inconnue')
            text = chunk.get('text', '')
            citation_key = self._extract_citation_key(source)
            context += f"\n[Source {i+1}: {citation_key}]\n{text}\n"
        
        prompt = f"""
Pour le cours "{course_title}", rédige 2 cas cliniques typiques avec leur correction.

Chaque cas doit inclure :
1. Présentation du patient (âge, sexe, motifs de consultation, antécédents)
2. Description des lésions (localisation, aspect, symptômes associés)
3. Questions (3-4 questions progressives : diagnostic différentiel, examens complémentaires, prise en charge)
4. Réponses détaillées avec justification bibliographique (cite les sources)

Utilise les données suivantes pour construire des cas réalistes :
{context}

Format final :
# Cas Cliniques

## Cas 1
**Présentation** : ...
**Questions** :
1. ...
**Réponses** :
1. ...

## Cas 2
...
"""
        messages = [HumanMessage(content=prompt)]
        response = self.llm.invoke(messages)
        return response.content

    def _generate_qcm(self, course_title: str, all_chunks: List[Dict]) -> str:
        """Génère un QCM de 10 questions avec corrections"""
        context = ""
        for i, chunk in enumerate(all_chunks[:25]):
            source = chunk.get('source', 'Source inconnue')
            text = chunk.get('text', '')
            citation_key = self._extract_citation_key(source)
            context += f"\n[Source {i+1}: {citation_key}]\n{text}\n"
        
        prompt = f"""
Pour le cours "{course_title}", crée un QCM de 10 questions avec réponses multiples (une ou plusieurs bonnes réponses par question).

Chaque question doit :
- Porter sur un point important du cours
- Avoir 4 propositions (A, B, C, D)
- Indiquer quelles sont les bonnes réponses
- Inclure une correction explicative avec référence bibliographique si possible

Utilise les données suivantes :
{context}

Format :
# QCM (10 questions)

## Question 1
[Énoncé]
A. [Proposition A]
B. [Proposition B]
C. [Proposition C]
D. [Proposition D]

**Réponses correctes** : A, C
**Explication** : ...

## Question 2
...
"""
        messages = [HumanMessage(content=prompt)]
        response = self.llm.invoke(messages)
        return response.content

    def generate_bibliography(self, all_chunks: List[Dict], markdown_text: str = "") -> str:
        """Génère la bibliographie : trials cliniques cités dans le texte + sources des chunks"""
        import re as _re
        seen = set()
        bib_lines = ["# Bibliographie\n"]
        idx = 1

        # 1. Extraire les trials cliniques citados dans le texte généré
        # Format: "> Essai : [Nom de l'essai] ([Auteurs], Année) — [Journal]"
        if markdown_text:
            trial_pattern = _re.compile(
                r'Essai\s*:\s*(.+?)\s*\(([A-Za-zÀ-ÿ\s\-\.]+?),\s*(\d{4})\)\s*[-—]\s*(.+?)(?=\n\s*(?:[>\#]|$))',
                _re.UNICODE | _re.DOTALL
            )
            for match in trial_pattern.finditer(markdown_text):
                trial_name = match.group(1) or ""
                authors = match.group(2) or ""
                year = match.group(3) or ""
                journal = match.group(4) or ""
                # Skip if "aucune donnée" or "non retrouvé" (guardrail fallback)
                skip_phrases = ["aucune donnée", "non retrouvé", "non disponible", "pas disponible"]
                full_essai = (trial_name + " " + authors).lower()
                if any(phrase in full_essai for phrase in skip_phrases):
                    continue
                if authors.strip() and year.strip():
                    key = f"{authors.strip()}, {year}"
                    if key not in seen:
                        seen.add(key)
                        journal_clean = journal.strip().rstrip(',').strip()
                        bib_lines.append(f"{idx}. {authors.strip()} ({year}) — {journal_clean}")
                        idx += 1

        # 2. Ajouter les sources des chunks (documents PDFs)
        for chunk in all_chunks:
            source = chunk.get('source', '')
            if source and source not in seen:
                seen.add(source)
                bib_lines.append(f"{idx}. {source.replace('.pdf', '')}")
                idx += 1

        if len(bib_lines) == 1:
            bib_lines.append("(Aucune source disponible)")
        return '\n'.join(bib_lines)

    def generate_full_course(self, course_title: str, retriever=None, level: str = "DES",
                             sections_enabled: Optional[List[str]] = None,
                             detail_level: str = "standard",
                             tone: str = "academique",
                             extra_instructions: str = "",
                             no_redundancy: bool = True,
                             focus_treatment_evidence: bool = True,
                             include_key_points: bool = False,
                             include_clinical_cases: bool = False,
                             include_qcm: bool = False) -> Tuple[str, int, int]:
        """
        Génère un cours complet avec toutes les sections.
        Returns: (markdown_content, sections_count, sources_count)
        """
        full_markdown = f"# {course_title}\n\n"
        full_markdown += f"*Cours de niveau {level} — Généré par DermatoRAG*\n\n---\n\n"

        all_chunks = []
        sections_generated = 0

        # Si sections_enabled est None, toutes les sections sont activées
        if sections_enabled is None:
            sections_enabled = [title for title, _ in SECTIONS]

        # Phase 1 : récupération des chunks (séquentiel, Qdrant n'est pas thread-safe en écriture)
        section_chunks = {}
        for section_title, keywords in SECTIONS:
            if section_title not in sections_enabled:
                continue
            chunks = []
            if retriever:
                try:
                    results1 = retriever.search(f"{course_title} {keywords}", top_k=10)
                    results2 = retriever.search(f"{section_title} dermatologie {course_title}", top_k=8)
                    query3_text = f"{course_title} {section_title.split()[0]}"
                    results3 = retriever.search(query3_text, top_k=5) if "Introduction" not in section_title else []
                    seen_texts = set()
                    for r in results1 + results2 + results3:
                        txt = r.get('text', '')
                        dedup_key = (r.get('source', ''), txt[:150])
                        if dedup_key not in seen_texts:
                            chunks.append(r)
                            seen_texts.add(dedup_key)
                    chunks = chunks[:15]
                except Exception as e:
                    print(f"Retrieval error: {e}", flush=True)
            section_chunks[section_title] = chunks
            all_chunks.extend(chunks)

        # Phase 2 : génération parallèle des sections (LLM I/O-bound)
        def _gen(section_title):
            print(f"Génération section: {section_title}...", flush=True)
            return section_title, self.generate_section(
                section_title=section_title,
                chunks=section_chunks[section_title],
                course_title=course_title,
                level=level,
                sections_enabled=sections_enabled,
                detail_level=detail_level,
                tone=tone,
                extra_instructions=extra_instructions,
                no_redundancy=no_redundancy,
                focus_treatment_evidence=focus_treatment_evidence,
                include_key_points=include_key_points,
                include_clinical_cases=False,
                include_qcm=False
            )

        ordered_titles = [t for t, _ in SECTIONS if t in section_chunks]
        results_map = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_gen, t): t for t in ordered_titles}
            for future in as_completed(futures):
                title, sec_content = future.result()
                results_map[title] = sec_content

        # Recompose dans l'ordre original
        for section_title in ordered_titles:
            sec_content = results_map.get(section_title, "")
            if sec_content:
                full_markdown += f"# {section_title}\n\n{sec_content}\n\n"
                sections_generated += 1

        # Ajouter les cas cliniques si demandé
        if include_clinical_cases:
            clinical_cases = self._generate_clinical_cases(course_title, all_chunks)
            full_markdown += "\n\n" + clinical_cases + "\n\n"

        # Ajouter le QCM si demandé
        if include_qcm:
            qcm = self._generate_qcm(course_title, all_chunks)
            full_markdown += "\n\n" + qcm + "\n\n"

        # Bibliography
        full_markdown += "\n" + self.generate_bibliography(all_chunks, full_markdown)

        # Count unique sources
        unique_sources = len(set(c.get('source', '') for c in all_chunks if c.get('source')))

        return full_markdown, sections_generated, unique_sources

    # Legacy method kept for compatibility
    def generate(self, query: str, context_chunks: List[Dict], course_title: str) -> str:
        return self.generate_section(course_title, context_chunks, course_title)
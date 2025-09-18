import os
import json
from openai import OpenAI
from typing import Dict, List, Optional
import time
import re

class GenericBoardStyleMedicalExplainer:
    def __init__(self, api_key: str):
        """Initialize the explainer with OpenAI API key"""
        self.client = OpenAI(api_key=api_key)
        self.research_results = []
        
    def parse_question(self, question_text: str) -> Dict:
        """
        Parse any medical board question to extract key components
        """
        # Extract the main topic and options
        lines = question_text.strip().split('\n')
        
        # Find the main question/topic (usually the first line or lines before numbered options)
        main_topic = ""
        options = []
        answer_choices = []
        correct_answer = ""
        
        current_section = "topic"
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Check if it's a numbered option (1), 2), 3), etc.)
            if re.match(r'^\d+\)', line):
                current_section = "options"
                options.append(line)
            
            # Check if it's answer choices (A., B., C., etc.)
            elif re.match(r'^[A-E]\.', line):
                current_section = "choices"
                answer_choices.append(line)
            
            # Check if it's the correct answer line (Polish or English)
            elif any(phrase in line for phrase in ["Prawidłowa odpowiedź", "Correct answer", "Answer:", "Odpowiedź:"]):
                current_section = "answer"
                correct_answer = line
            
            # Build the main topic
            elif current_section == "topic":
                main_topic += " " + line
        
        # Clean up the main topic
        main_topic = main_topic.strip().rstrip(':')
        
        return {
            "main_topic": main_topic,
            "options": options,
            "answer_choices": answer_choices,
            "correct_answer": correct_answer
        }

    def extract_medical_keywords(self, question_data: Dict) -> List[str]:
        """
        Extract key medical terms for targeted searching (Polish-aware)
        """
        extraction_prompt = f"""
        Analizuj to pytanie egzaminacyjne z medycyny i wyodrębnij najważniejsze słowa kluczowe i pojęcia medyczne do celowego wyszukiwania:

        GŁÓWNY TEMAT: {question_data['main_topic']}
        OPCJE: {' '.join(question_data['options'])}
        PRAWIDŁOWA ODPOWIEDŹ: {question_data['correct_answer']}

        Wyodrębnij:
        1. Główny stan/procedurę/temat medyczny (po polsku i angielsku)
        2. Kluczowe terminy i procedury medyczne
        3. Powiązane struktury anatomiczne
        4. Kategorie leczenia lub klasyfikacje
        5. Kryteria diagnostyczne lub metody
        6. Odpowiednie wytyczne lub towarzystwa (WHO, PTK, PTO, ESC, itp.)

        Formatuj jako listę konkretnych terminów wyszukiwania odpowiednich do przeszukiwania literatury medycznej.
        Uwzględnij terminy zarówno po polsku, jak i po angielsku.
        Priorytetowo traktuj terminy, które pomogą znaleźć oficjalne wytyczne i najnowszą literaturę medyczną.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Jesteś bibliotekarzem medycznym specjalizującym się w wyodrębnianiu terminów wyszukiwania do literatury medycznej. Wyodrębnij precyzyjną terminologię medyczną do ukierunkowanych wyszukiwań w bazach danych. Odpowiadaj po polsku, ale uwzględnij również angielskie terminy medyczne."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.1,
                max_tokens=800
            )
            
            keywords_text = response.choices[0].message.content
            # Simple extraction - in practice, you might want more sophisticated parsing
            keywords = [line.strip() for line in keywords_text.split('\n') if line.strip() and not line.startswith('#')]
            
            return keywords[:8]  # Limit to top 8 search terms
            
        except Exception as e:
            print(f"Keyword extraction error: {e}")
            return ["ogólny temat medyczny", "general medical topic"]

    def targeted_medical_search(self, question_data: Dict, keywords: List[str]) -> str:
        """
        Performs targeted web search based on extracted keywords (Polish-aware)
        """
        # Create targeted search queries based on keywords
        search_queries = []
        
        # Add guideline searches (Polish and international)
        for keyword in keywords[:3]:  # Top 3 keywords
            search_queries.append(f"{keyword} wytyczne 2023 2024 oficjalne zalecenia")
            search_queries.append(f"{keyword} guidelines 2023 2024 official recommendations")
            search_queries.append(f"{keyword} kryteria klasyfikacja diagnostyka")
        
        # Add specific Polish medical society searches if relevant
        topic_lower = question_data['main_topic'].lower()
        if any(term in topic_lower for term in ['jaskra', 'oko', 'oczny', 'glaucoma', 'eye', 'ocular']):
            search_queries.append("PTO Polskie Towarzystwo Okulistyczne wytyczne")
            search_queries.append("AAO American Academy Ophthalmology guidelines 2023")
            search_queries.append("EGS European Glaucoma Society guidelines")
        elif any(term in topic_lower for term in ['serce', 'sercowy', 'kardiologia', 'cardiac', 'heart', 'cardiovascular']):
            search_queries.append("PTK Polskie Towarzystwo Kardiologiczne wytyczne")
            search_queries.append("ESC European Society Cardiology guidelines 2023")
            search_queries.append("AHA American Heart Association guidelines")
        
        search_prompt = f"""
        Przeprowadź kompleksowe wyszukiwania internetowe w celu znalezienia aktualnych, wiarygodnych informacji medycznych do odpowiedzi na to pytanie egzaminacyjne:

        PYTANIE: {question_data['main_topic']}
        OPCJE: {' '.join(question_data['options'])}
        PRAWIDŁOWA ODPOWIEDŹ: {question_data['correct_answer']}
        
        STRATEGIA WYSZUKIWANIA - Znajdź najbardziej istotne dane dla każdego komponentu:

        **PRIORYTETOWE WYSZUKIWANIA:**
        {chr(10).join([f"• {query}" for query in search_queries])}

        **KRYTYCZNE WYMAGANIA DOTYCZĄCE WYODRĘBNIANIA URL:**
        MUSISZ znaleźć i zwrócić RZECZYWISTE, DZIAŁAJĄCE adresy URL dla każdego źródła. Nie używaj tekstów zastępczych.

        **SZUKAJ:**
        1. **Oficjalne Wytyczne i Klasyfikacje**: Aktualne rekomendacje towarzystw medycznych
        2. **Kryteria Diagnostyczne**: Konkretne kryteria dla każdej wymienionej opcji
        3. **Klasyfikacje Leczenia**: Jak procedury/leczenie są kategoryzowane
        4. **Baza Dowodów**: Najnowsze badania wspierające lub obalające każdą opcję
        5. **Dane Porównawcze**: Różnice między różnymi przedstawionymi opcjami

        **WYMAGANY FORMAT WYJŚCIA:**
        Dla KAŻDEGO znaleziska wyszukiwania:
        **FAKT MEDYCZNY**: [konkretne informacje kliniczne]
        **ŹRÓDŁO**: [nazwa oficjalnych wytycznych/czasopisma z rokiem]
        **RZECZYWISTY URL**: [kompletny działający URL zaczynający się od https://]
        **TRAFNOŚĆ**: [jak to wspiera/obala konkretne opcje odpowiedzi]

        **KRYTYCZNE**: NIE pisz "link" lub "[URL]" - znajdź i zwróć rzeczywiste adresy internetowe.
        Skoncentruj się na informacjach, które wyjaśniają, dlaczego określone opcje są prawidłowe/nieprawidłowe.
        
        Odpowiadaj po polsku, ale uwzględnij również źródła międzynarodowe.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Jesteś specjalistą ds. badań medycznych, który znajduje RZECZYWISTE działające adresy URL źródeł medycznych. Zawsze zwracaj kompletne adresy internetowe zaczynające się od https://. Skoncentruj się na oficjalnych wytycznych i wiarygodnej literaturze medycznej. Odpowiadaj po polsku."},
                    {"role": "user", "content": search_prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            
            search_results = response.choices[0].message.content
            self.research_results.append({
                "type": "targeted_search",
                "question": question_data['main_topic'],
                "content": search_results,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            
            return search_results
            
        except Exception as e:
            print(f"Search error: {e}")
            return f"Wyszukiwanie nie powiodło się: {str(e)}"

    def generate_clinical_vignette(self, question_data: Dict, research_data: str) -> str:
        """
        Generate a clinical vignette based on the question topic (Polish)
        """
        vignette_prompt = f"""
        Stwórz realistyczną scenę kliniczną dla tego tematu egzaminacyjnego z medycyny:

        TEMAT: {question_data['main_topic']}
        DANE Z BADAŃ: {research_data[:1000]}...

        Stwórz scenariusz kliniczny składający się z 2-3 zdań, który:
        1. Przedstawia realistyczny przypadek pacjenta
        2. Zawiera kluczowe objawy kliniczne istotne dla pytania
        3. Ustanawia kontekst dla decyzji terapeutycznych/diagnostycznych, które są testowane

        Niech będzie w stylu egzaminu państwowego i klinicznie realistyczny.
        Odpowiadaj po polsku.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Jesteś edukatorem medycznym tworzącym realistyczne scenariusze kliniczne do przygotowania do egzaminów państwowych. Twórz scenariusze klinicznie dokładne i istotne dla testowanego pytania. Odpowiadaj po polsku."},
                    {"role": "user", "content": vignette_prompt}
                ],
                temperature=0.2,
                max_tokens=300
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return "Generowanie scenariusza klinicznego nie powiodło się."

    def generate_explanation(self, question: str, options: List[str], correct_answer: str) -> str:
        """
        Generate a comprehensive board-style explanation for a medical question (Polish)
        """
        # Format the question for processing
        question_text = f"{question}\n"
        
        # Add options if provided
        if options:
            for i, option in enumerate(options):
                question_text += f"{chr(65+i)}. {option}\n"
        
        # Add correct answer if provided
        if correct_answer:
            question_text += f"\nPrawidłowa odpowiedź: {correct_answer}"
        
        # Parse the question
        question_data = self.parse_question(question_text)
        
        # Extract keywords for targeted search
        keywords = self.extract_medical_keywords(question_data)
        
        # Conduct targeted research
        research_data = self.targeted_medical_search(question_data, keywords)
        
        # Generate clinical vignette
        clinical_vignette = self.generate_clinical_vignette(question_data, research_data)
        
        # Create comprehensive explanation
        comprehensive_prompt = f"""
        Stwórz kompleksowe wyjaśnienie w stylu egzaminu państwowego z medycyny, stosując sprawdzony schemat:

        ORYGINALNE PYTANIE:
        {question_text}

        PRZEANALIZOWANE DANE PYTANIA:
        Główny Temat: {question_data['main_topic']}
        Opcje: {question_data['options']}
        Wybory Odpowiedzi: {question_data['answer_choices']}
        Prawidłowa Odpowiedź: {question_data['correct_answer']}

        DANE Z BADAŃ:
        {research_data}

        SCENARIUSZ KLINICZNY:
        {clinical_vignette}

        **Stwórz DOSKONAŁE wyjaśnienie w stylu egzaminu państwowego ze WSZYSTKIMI niezbędnymi elementami:**

        # Wyjaśnienie Medyczne w Stylu Egzaminu Państwowego

        ## **Scenariusz Kliniczny**
        **OBOWIĄZKOWY SCENARIUSZ:**
        {clinical_vignette}
        
        **Ta prezentacja stanowi przykład kluczowego kontekstu klinicznego dla tego tematu egzaminacyjnego.**

        ## **Testowana Główna Koncepcja**
        [Zidentyfikuj, jaką konkretną wiedzę/umiejętność medyczną testuje to pytanie]

        ## **Kluczowe Wskazówki Kliniczne → Łańcuch Diagnozy/Decyzji**
        **Niezbędne Terminy Medyczne:** [Wymień kluczowe słowa kluczowe i terminologię]
        [Połącz objawy kliniczne z procesem podejmowania decyzji medycznych]

        ## **Aktualne Wytyczne i Dowody**
        [Cytuj odpowiednie wytyczne medyczne z dowodami wspierającymi z badań]

        ## **Dlaczego Prawidłowa Odpowiedź jest Słuszna**
        [Przeanalizuj każdą prawidłową opcję z dowodami wspierającymi z badań]

        ## **Dlaczego Błędne Opcje są Nieprawidłowe**
        [Systematycznie wykluczaj każdą nieprawidłową opcję z rozumowaniem opartym na dowodach]

        ## **TABELA PORÓWNAWCZA ULUBIONA NA EGZAMINACH**
        [Stwórz odpowiednią tabelę porównawczą opartą na temacie - leczenie, diagnozy, klasyfikacje itp.]

        ## **Perły o Wysokiej Wydajności i Fakty Kliniczne**
        [Uwzględnij kluczowe fakty, które są powszechnie testowane na egzaminach państwowych]

        ## **Haczyk do Zapamiętania**
        [Stwórz zapadającą w pamięć frazę lub akronim dla tego tematu]

        ## **Główna Wiadomość**
        [Podsumowanie jednej linii z kluczową zasadą kliniczną]

        ## **SZYBKIE PRZYPOMNIENIE - PODSUMOWANIE**
        **SZYBKA STRZAŁKA EGZAMINU PAŃSTWOWEGO:**
        **PRAWIDŁOWE:** [Wymień prawidłowe opcje z krótkim uzasadnieniem]
        **BŁĘDNE:** [Wymień nieprawidłowe opcje z krótkim uzasadnieniem]
        **URZĄDZENIE PAMIĘCIOWE:** [Szybka metoda przypominania]

        ## **Przypisy do Dowodów**
        [Wymień wszystkie źródła z cytowaniami z danych badawczych]

        **WYMAGANIA:**
        - 600-800 słów wysokiej jakości treści
        - Każdy fakt medyczny wspierany danymi z badań
        - Język i struktura w stylu egzaminu państwowego
        - Kompleksowe rozumowanie dla wszystkich wyborów odpowiedzi
        - Odpowiednia tabela porównawcza
        - Integracja scenariusza klinicznego
        - Wszystko po polsku
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Jesteś ekspertem edukatorem medycznym tworzącym kompleksowe wyjaśnienia w stylu egzaminu państwowego. Uwzględnij WSZYSTKIE wymagane elementy: scenariusz kliniczny, tabelę porównawczą, szybkie przypomnienie i cytowania dowodów. Czyń wyjaśnienia kompletne i skoncentrowane na egzaminie państwowym. Odpowiadaj WYŁĄCZNIE po polsku."},
                    {"role": "user", "content": comprehensive_prompt}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            explanation = response.choices[0].message.content
            
            return explanation
            
        except Exception as e:
            print(f"Error in explanation generation: {str(e)}")
            return f"Generowanie wyjaśnienia nie powiodło się: {str(e)}"

    def generate_simple_explanation(self, question: str, options: List[str], correct_answer: str) -> str:
        """
        Generate a simpler explanation suitable for database storage (Polish)
        """
        # Format options as labeled choices
        labeled_options = []
        for i, option in enumerate(options):
            labeled_options.append(f"{chr(65+i)}. {option}")
        
        prompt = f"""
        Dostarcz jasne, zwięzłe wyjaśnienie medyczne dla tego pytania:

        Pytanie: {question}
        
        Opcje:
        {chr(10).join(labeled_options)}
        
        Prawidłowa odpowiedź: {correct_answer}

        Wymagania:
        1. Wyjaśnij, dlaczego prawidłowa odpowiedź jest słuszna
        2. Krótko wyjaśnij, dlaczego inne opcje są nieprawidłowe
        3. Uwzględnij kluczowe fakty medyczne i rozumowanie
        4. Zachowaj koncentrację i wartość edukacyjną
        5. Używaj profesjonalnego języka medycznego
        6. Dąż do 200-400 słów
        7. Odpowiadaj WYŁĄCZNIE po polsku

        Sformatuj jako jasne, dobrze ustrukturyzowane wyjaśnienie odpowiednie dla studentów medycyny.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Jesteś edukatorem medycznym dostarczającym jasne, dokładne wyjaśnienia dla pytań w stylu egzaminu państwowego. Skoncentruj się na wartości edukacyjnej i rozumowaniu klinicznym. Odpowiadaj WYŁĄCZNIE po polsku używając polskiej terminologii medycznej."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"Error in simple explanation generation: {str(e)}")
            return f"Nie można wygenerować wyjaśnienia: {str(e)}"
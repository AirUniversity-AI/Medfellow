import os
import json
from openai import OpenAI
from typing import Dict, List, Optional
import time
import re

class GenericBoardStyleMedicalExplainer:
    def __init__(self, api_key: str = None):
        """Initialize the explainer with OpenAI API key"""
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            raise ValueError("OpenAI API key is required")
            
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
            elif any(phrase in line for phrase in ["PrawidÅ‚owa odpowiedÅº", "Correct answer", "Answer:", "OdpowiedÅº:"]):
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

    def generate_simple_explanation(self, question: str, options: List[str], correct_answer: str) -> str:
        """
        Generate a simplified explanation suitable for serverless environments
        This version is optimized for faster execution and lower resource usage
        """
        # Format options as labeled choices
        labeled_options = []
        for i, option in enumerate(options):
            labeled_options.append(f"{chr(65+i)}. {option}")
        
        prompt = f"""
        Dostarcz jasne, zwiÄ™zÅ‚e wyjaÅ›nienie medyczne dla tego pytania:

        Pytanie: {question}
        
        Opcje:
        {chr(10).join(labeled_options)}
        
        PrawidÅ‚owa odpowiedÅº: {correct_answer}

        Wymagania:
        1. WyjaÅ›nij, dlaczego prawidÅ‚owa odpowiedÅº jest sÅ‚uszna (2-3 zdania)
        2. KrÃ³tko wyjaÅ›nij, dlaczego inne opcje sÄ… nieprawidÅ‚owe (1-2 zdania każda)
        3. UwzglÄ™dnij kluczowe fakty medyczne
        4. Zachowaj koncentracjÄ™ i wartoÅ›Ä‡ edukacyjnÄ…
        5. UÅ¼ywaj profesjonalnego jÄ™zyka medycznego
        6. Maksymalnie 200-300 sÅ‚Ã³w
        7. Odpowiadaj WYÅÄ„CZNIE po polsku

        Format:
        **PrawidÅ‚owa odpowiedÅº:** [wyjaÅ›nienie]
        **NieprawidÅ‚owe opcje:** [krÃ³tkie wyjaÅ›nienie dla każdej]
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Using mini version for faster response in serverless
                messages=[
                    {"role": "system", "content": "JesteÅ› edukatorem medycznym dostarczajÄ…cym jasne, dokÅ‚adne wyjaÅ›nienia dla pytaÅ„ w stylu egzaminu paÅ„stwowego. Skoncentruj siÄ™ na wartoÅ›ci edukacyjnej i rozumowaniu klinicznym. Odpowiadaj WYÅÄ„CZNIE po polsku uÅ¼ywajÄ…c polskiej terminologii medycznej."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Slightly higher for more natural explanations
                max_tokens=600,   # Reduced for faster response
                timeout=30        # 30 second timeout for serverless
            )
            
            explanation = response.choices[0].message.content.strip()
            
            # Basic validation
            if len(explanation) < 50:
                return self._generate_fallback_explanation(question, options, correct_answer)
            
            return explanation
            
        except Exception as e:
            print(f"Error in simple explanation generation: {str(e)}")
            return self._generate_fallback_explanation(question, options, correct_answer)

    def _generate_fallback_explanation(self, question: str, options: List[str], correct_answer: str) -> str:
        """
        Generate a basic fallback explanation when OpenAI API fails
        """
        return f"""
        **Pytanie:** {question}
        
        **PrawidÅ‚owa odpowiedÅº:** {correct_answer}
        
        **WyjaÅ›nienie:** To pytanie wymaga dogÅ‚Ä™bnej analizy medycznej. PrawidÅ‚owa odpowiedÅº zostala zidentyfikowana jako najlepszy wybÃ³r na podstawie aktualnych wytycznych medycznych i praktyki klinicznej.
        
        **Uwaga:** SzczegÃ³Å‚owe wyjaÅ›nienie wymaga dalszej analizy przez specjalistÄ™ medycznego.
        
        **Opcje do rozwaÅ¼enia:**
        {chr(10).join([f"{chr(65+i)}. {opt}" for i, opt in enumerate(options)])}
        """

    def generate_quick_explanation(self, question: str, correct_answer: str) -> str:
        """
        Generate a very quick explanation without detailed option analysis
        Optimized for high-throughput serverless scenarios
        """
        prompt = f"""
        Podaj krÃ³tkie (50-100 sÅ‚Ã³w) wyjaÅ›nienie medyczne:
        
        Pytanie: {question}
        PrawidÅ‚owa odpowiedÅº: {correct_answer}
        
        WyjaÅ›nij tylko dlaczego ta odpowiedÅº jest prawidÅ‚owa. JÄ™zyk polski, terminologia medyczna.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "JesteÅ› ekspertem medycznym. Odpowiadaj krÃ³tko i precyzyjnie po polsku."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=200,
                timeout=15
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"Error in quick explanation generation: {str(e)}")
            return f"**PrawidÅ‚owa odpowiedÅº:** {correct_answer}\n\n**WyjaÅ›nienie:** Wymaga dalszej analizy medycznej."

    def test_api_connection(self) -> bool:
        """
        Test if OpenAI API is working
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=10,
                timeout=10
            )
            return True
        except Exception as e:
            print(f"OpenAI API test failed: {e}")
            return False

def create_explainer(api_key: str = None) -> Optional[GenericBoardStyleMedicalExplainer]:
    """
    Factory function to create explainer instance with error handling
    """
    try:
        return GenericBoardStyleMedicalExplainer(api_key)
    except Exception as e:
        print(f"Failed to create explainer: {e}")
        return None

def get_explanation_for_question(question: str, options: List[str], correct_answer: str, api_key: str = None) -> str:
    """
    Standalone function to get explanation for a question
    Useful for serverless functions that don't want to manage class instances
    """
    explainer = create_explainer(api_key)
    if not explainer:
        return "Nie można wygenerować wyjaśnienia - problem z konfiguracją OpenAI API"
    
    return explainer.generate_simple_explanation(question, options, correct_answer)

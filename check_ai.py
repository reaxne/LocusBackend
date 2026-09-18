"""Manual free API smoke check; synthetic data only, no secret/provider-body output.

Run: python check_ai.py [--purpose roadmap|recommendations|profile|all]
"""
import argparse
import json
import time
from settings import load_environment
from recommendation.ai import AIUnavailable
from recommendation.free_ai import FreeAIClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--purpose', choices=['roadmap', 'recommendations', 'profile', 'all'], default='all')
    args = parser.parse_args()
    load_environment()
    for purpose in (['roadmap', 'recommendations', 'profile'] if args.purpose == 'all' else [args.purpose]):
        context = {'student': {'grade': 10, 'entryYear': 2028, 'interest': ['Программирование'],
                               'academicStrengths': ['Математика'], 'IELTS': 5.5},
                   'planning': {'examGoals': {'IELTS': {'targetScore': 6.5, 'targetDate': None}}},
                   'results': {'recommendations': [{'programId': 'synthetic-program',
                       'program': 'Вымышленная учебная программа', 'whyRecommended': ['Интерес к программированию'],
                       'roadmap': [{'id': 'synthetic-project', 'title': 'Учебный проект',
                                    'reason': 'Необязательное развитие интересов.'}]}]}}
        client = FreeAIClient()
        started = time.monotonic()
        try:
            client.generate(context, purpose)
            status = 'generated_and_validated'
        except AIUnavailable as exc:
            status = str(exc)
        print(json.dumps({'purpose': purpose, 'status': status, 'model': client.model,
                          'durationMs': round((time.monotonic()-started)*1000),
                          'attempts': client.attempts}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

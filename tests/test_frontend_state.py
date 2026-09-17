from fastapi.testclient import TestClient
from main import create_app
from profile_schema import ApplicantProfile, QUESTIONS, to_survey

HEADERS = {'Origin':'http://127.0.0.1:5173', 'X-Locus-Request':'1'}


def profile():
    return dict(grade=11, entryYear=2027, interest='Software engineering', city='Astana',
        mustStay=False,budget=None,funding='either',category='unknown',country='Kazakhstan',level='bachelor',
        studyLanguage='any',academicPerformance='unknown',constraints='',academicStrengths=[],extracurricularInterests=[],
        exams={key:{'status':'unknown','score':None} for key in ['SAT','IELTS','NUET','UNT','AET']},
        examGoals={key:{'targetScore':None,'targetDate':None} for key in ['SAT','IELTS','NUET','UNT']},
        ieltsSectionScores={key:None for key in ['Listening','Reading','Writing','Speaking']})


def signup(client, username='student'):
    creds = {'username': username,'password':'long-password-123'}
    user = client.post('/auth/register',json=creds).json()
    response = client.post('/auth/login',json=creds)
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    client.headers['X-Locus-User'] = str(user['id'])
    return user


def payload(revision=0, complete=False):
    p = profile()
    return {'survey':to_survey(ApplicantProfile(**p)), 'state':{'draft':p,'profile':p if complete else None,
        'draftStep':17 if complete else 1,'answeredQuestions':QUESTIONS if complete else ['grade'],'revision':revision}}


def test_browser_cookie_draft_submit_edit_conflict_and_reload(db_url, monkeypatch):
    monkeypatch.setenv('COOKIE_SECURE','false')
    with TestClient(create_app(db_url), headers=HEADERS) as client:
        signup(client)
        assert client.get('/survey').status_code == 404
        response = client.post('/survey',json=payload())
        assert response.status_code == 200, response.text
        assert response.json()['state']['profile'] is None
        assert client.get('/survey').json()['state']['draftStep'] == 1
        assert client.get('/recommendations').status_code == 409
        assert client.post('/survey',json=payload(1,True)).json()['state']['revision'] == 2
        update = payload(2,True)
        update['state']['profile']['constraints'] = 'Prefer accessible housing'
        update['state']['draft']['constraints'] = 'Prefer accessible housing'
        assert client.post('/survey',json=update).status_code == 200
        assert client.post('/survey',json=update).status_code == 409
        assert client.get('/survey').json()['state']['profile']['constraints'] == 'Prefer accessible housing'
        assert client.get('/recommendations').status_code == 200
        assert client.post('/auth/logout').status_code == 204
        assert client.get('/survey').status_code == 401
        client.post('/auth/login',json={'username':'student','password':'long-password-123'})
        assert client.get('/survey').json()['state']['revision'] == 3


def test_validation_csrf_and_account_switch(db_url, monkeypatch):
    monkeypatch.setenv('COOKIE_SECURE','false')
    with TestClient(create_app(db_url),headers=HEADERS) as client:
        first = signup(client)
        bad = payload()
        bad['state']['draft']['exams']['IELTS'] = {'status':'completed','score':10}
        assert client.post('/survey',json=bad).status_code == 422
        assert client.post('/survey',json=payload(),headers={'Origin':'https://evil.example'}).status_code == 403
        assert client.post('/survey',json=payload(),headers={'X-Locus-Request':''}).status_code == 403
        assert client.post('/survey',json=payload()).status_code == 200
        signup(client,'second')
        assert client.get('/survey').status_code == 404
        assert client.post('/survey',json=payload(),headers={'X-Locus-User':str(first['id'])}).status_code == 409

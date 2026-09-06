Application_keywords=["application","shortlisted","interview","rejected","offer","selected","received","hired","applied","screening","assessment","evaluation","candidate","job","position","vacancy","employment","career","resume","CV","cover letter","job description","job posting"]
for i in range(len(Application_keywords)):
    Application_keywords[i]=Application_keywords[i].lower()

def potential_email(header,body):
    inputs= f"{header},{body}".lower()
    match=[]
    for i in range(len(Application_keywords)):
        if Application_keywords[i] in inputs:
            match.append(Application_keywords[i])
    if len(match)>0:
        print("Potential job application status email detected.")
        return True
    else:
        print("Potential job application status email not detected.")
        return False



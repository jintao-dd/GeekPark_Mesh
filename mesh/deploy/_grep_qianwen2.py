from app import db
con = db.connect()
t = con.execute("SELECT text FROM sources WHERE id=23").fetchone()["text"]
for needle in ["沟通对象: 千问", "阿里千问", "千问（公关", "智能体相关动态"]:
    i = t.find(needle)
    print(needle, "->", i)
    if i >= 0:
        print(t[max(0,i-200):i+500])
        print("---")
con.close()

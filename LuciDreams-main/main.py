from flask import Flask, render_template, redirect

app = Flask(__name__)


@app.route('/')
def root():
    return redirect('/luci')


@app.route('/luci')
def luci():
    return render_template('luci.html')


@app.route('/researcher-setup')
def researcher_setup():
    return render_template('researcher-setup.html')


@app.route('/wake')
def wake():
    return redirect('/luci')


@app.route('/wakecomplete')
def wakecomplete():
    return redirect('/luci')


if __name__ == '__main__':
    # This is used when running locally only. When deploying to Google App
    # Engine, a webserver process such as Gunicorn will serve the app. This
    # can be configured by adding an `entrypoint` to app.yaml.
    # Flask's development server will automatically serve static files in
    # the "static" directory. See:
    # http://flask.pocoo.org/docs/1.0/quickstart/#static-files. Once deployed,
    # App Engine itself will serve those files as configured in app.yaml
    app.run(host='localhost', port=8080, debug=True)
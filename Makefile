.PHONY: image test run

image:
	docker build -t nerv-sandbox:latest sandbox/

test:
	python3 -m pytest -q

run: image
	python3 -m harness.run tasks/001-fizzbuzz

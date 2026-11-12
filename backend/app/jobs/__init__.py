"""Cola de jobs respaldada por BD, worker y pipeline prompt->video.

Cola en proceso respaldada por la tabla ``Job`` con workers; granularidad por escena.
Se implementa en FEAT-002 (cola/worker) y FEAT-006 (pipeline end-to-end).
"""

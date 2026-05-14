from production.parser import TreeBuilder

text = """
1. A method for enzymatic conversion comprising reacting A to B.
2. The method of claim 1, wherein the enzyme has at least 90% identity to SEQ ID NO:1.
3. The method of claim 1 or 2, wherein the reaction is performed at pH 7.
"""

nodes = TreeBuilder.parse_claims(text)
for n in nodes:
    print(n.model_dump())

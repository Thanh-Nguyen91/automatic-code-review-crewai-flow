from pydantic import BaseModel
from crewai import LLM
from crewai.flow import Flow, listen, start, router, or_, persist
# from typing import Literal, Optional
from code_review_flow.crews.code_review_crew.crew import CodeReviewCrew
import json

import os
os.environ["CREWAI_TESTING"] = "true"

# define the flow state
class ReviewState(BaseModel):
    pr_file_path: str = ''
    pr_content: str = '' 
    errors: list = [] 
    review_result: dict = {} 
    crew_needed: bool = False 
    tokens_used: dict = {}
    final_answer: str = '' 
    ### END CODE HERE ###


@persist()
# Define the flow
class PRCodeReviewFlow(Flow[ReviewState]):
    """
    CrewAI Flow for automated code review of pull requests.
    """
    @start()
    def read_pr_file(self):
        """Read the PR file and determine if crew review is needed"""
        print("🔍 Starting PR Code Review...")
        
        # Get the file path from the state
        pr_file_path = self.state.pr_file_path

        if not pr_file_path:
            # append some error message to the errors state variable
            self.state.errors.append("Missing 'file_path' in state") 
            # save the error message in the final answer state variable
            self.state.final_answer = f"There was no file_path set. Please set the 'file_path' in the state and try again." 
            print(f"❌ {self.state.final_answer}")
            return

        # try reading the file
        try:
            with open(pr_file_path, "r") as f:
                file_contents = f.read()
            
            # save the file content in the state variable
            self.state.pr_content = file_contents
               
        # if there are any issues, raise an error
        except Exception as e:
            error_message = f"There was an error reading the file at {pr_file_path}: \n{str(e)}"
            print(f"❌ {error_message}")
            # append some error message to the errors state variable
            self.state.errors.append("Error while reading the PR file")
            # save the error message in the final answer state variable
            self.state.final_answer = error_message


    @router("read_pr_file")
    def analyze_changes(self, context):
        """Route to appropriate review type based on complexity"""

        # if there are any errors in the state variable, return 'ERROR'
        if len(self.state.errors) > 0:
            return 'ERROR'

        else:
            # define the prompt to analyze the changes
            prompt = (
                "Analyze this pull request diff file and respond with exactly one word: SIMPLE or COMPLEX.\n"
                "SIMPLE: small changes that don't compromise code quality or security. For example typos, minor refactoring, formatting, small doc changes"
                "COMPLEX: bigger changes that need closer inspection. For example new features, bug fixes, logic changes, security-relevant changes, or anything requiring deep review or best practices research.\n"
                f"\nPR Diff:\n{self.state.pr_content}\n"
                )

            # define the llm for the decision 
            llm = LLM(model="gpt-4o-mini",) 
            # call the llm and save the result
            decision = llm.call(messages=prompt) 
            
            # if the word COMPLEX is in the decision, 
            # set the crew_needed to True in the state and return "COMPLEX"
            if "COMPLEX" in decision.upper():
                self.state.crew_needed = True
                return 'COMPLEX'
            # if not COMPLEX (i.e. SIMPLE)
            # set the crew_needed to False in the state and return "SIMPLE"
            else:
                self.state.crew_needed = False
                return 'SIMPLE'

    # if the PR is simple, do a simple review
    @listen("SIMPLE")
    def simple_review(self):
        """Simple review for minor changes"""
        print("⚡ Performing simple review...")
        
        prompt = (
            "Analyze this pull request diff file and evaluate the changes.\n"
            "Do not make assumptions or considerations about the code outside of the diff provided, but if warranted you can make suggestions.\n"
            """
            return a JSON with keys: 
    'confidence' (confidence score of the code changes, as integer between 0 and 100),
    'findings' (A summary of the key findings, as string),
    'fix' (A list of what must be fixed, and possible solutions, and the explanation for this fixes), and
    'recommendations' (Any additional recommendations or observations, as string).\n
            """
            f"\nPR Diff:\n{self.state.pr_content}\n"
        )
        

        # define the llm for the decision 
        llm = LLM(model="gpt-4o-mini",)
        # call the llm and save the result
        result = llm.call(messages=prompt)

        
        # Save the result of the LLM call in the review_result state variable
        self.state.review_result = result
       

    # if the PR is complex, deploy crew review
    @listen("COMPLEX")
    def full_crew_review(self):
        """Full crew review for complex changes"""
        print("🚀 Starting full crew review...")
        
        # get the PR content from the state variable
        pr_content = self.state.pr_content

        # create the crew
        code_review_crew = CodeReviewCrew().crew()

        try: 
            
            # kickoff the crew. Pass the PR content in the inputs
            result = code_review_crew.kickoff(inputs={'file_content': pr_content})

            # save the results in the state variable
            self.state.review_result = result.json_dict

            # save the tokens used by the crew
            self.state.tokens_used = result.token_usage
            
        except Exception as e:
            error_message = f"There was an error during the crew review: \n{str(e)}"
            print(f"❌ {error_message}")
            
            # append some error message to the errors state variable
            self.state.errors.append("Error during crew review")
            # save the error message in the final answer state variable
            self.state.final_answer = error_message
            

    # make the final decision based on the review results
    
    @listen(or_("simple_review","full_crew_review" ))
    def make_final_decision(self):
        """Make the final decision based on the review results"""
        print("🧐 Making final decision based on review results...")
        
        # get the review result from the state variable
        review_result = self.state.review_result

        # use an LLM call to make the final decision and generate the final message
        prompt = (
            "Based on the following analysis of the pull request diff file, "
            "make a final decision on whether to approve the PR for merging.\n" \
            "Any review with a confidence score above 85 can be approves, but improvements can be suggested.\n"
            "Return a full report with:\n"
            "- Final Review Decision: APPROVE (the PR is good to be approved), REQUEST CHANGES (the PR needs some modifications, but they are concrete changes) " 
            " or ESCALATE (the PR requires human attention, there are major issues that need fixing)\n"
            "- Confidence Score: int. A confidence score between 0-100 that indicates the confidence for merging the PR\n"
            "- Findings: str. A summary of the key findings\n"
            "- If the decision is to REQUEST CHANGES, provide a list of the changes requested to grant approval\n"
            "- If the decision is to ESCALATE, provide a list of reasons why the PR needs to be escalated to a human reviewer, along with possible solutions\n"
            f"\nAnalysis:\n{review_result}\n"
        )
        llm = LLM(model="gpt-4o-mini",)
        
        
        # make the LLM call
        final_decision = llm.call(messages=prompt)
        # save the final answer in the state variable
        self.state.final_answer = final_decision
        
    # return the final answer
    @listen(or_("ERROR", "make_final_decision"))
    def return_final_answer(self):
        """Return the final answer to the user"""

        print("📝 Final Answer:")
        print(f"{self.state.final_answer}")
        print("\n✨ Automatic code review completed!")
        return self.state.final_answer


def kickoff():
    
    # instantiate the flow with tracing enabled
    pr_code_review_flow = PRCodeReviewFlow(tracing=True)
    # kickoff the flow
    result = pr_code_review_flow.kickoff(inputs={
        "id": "1",
        "pr_file_path": "../files/code_changes.txt"
        })
    
    # Save the flow state as JSON
    with open('../flow_state.json', 'w') as f:
        json.dump(pr_code_review_flow.state.model_dump(), f, indent=2)


def plot():
    pr_code_review_flow = PRCodeReviewFlow()
    pr_code_review_flow.plot()


if __name__ == "__main__":
    kickoff()